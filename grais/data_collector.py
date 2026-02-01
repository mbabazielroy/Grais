"""
Automated Data Collection Module for GRAIS

Collects real-world data from public sources:
- Government open data portals
- Humanitarian data exchanges
- Public APIs
- Census/population data

Features:
- Rate limiting to respect server limits
- Caching to avoid redundant requests
- Data validation and transformation
- Multiple source support

Usage:
    from grais.data_collector import DataCollector
    
    collector = DataCollector(cache_dir="./cache")
    
    # Collect population data
    regions = collector.collect_canada_population()
    
    # Collect from humanitarian sources
    regions = collector.collect_humanitarian_data("canada")
    
    # Auto-collect from multiple sources
    regions, depots = collector.auto_collect(country="canada", region="ontario")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from grais.data_generator import RegionData, Depot

logger = logging.getLogger("grais.data_collector")

# =============================================================================
# Configuration
# =============================================================================

# Public data source URLs
DATA_SOURCES = {
    # Statistics Canada
    "statscan_population": "https://www12.statcan.gc.ca/rest/census-recensement/CR2021Geo.json",
    
    # Ontario Open Data
    "ontario_health_units": "https://data.ontario.ca/api/3/action/datastore_search",
    "ontario_population": "https://data.ontario.ca/api/3/action/datastore_search",
    
    # Humanitarian Data Exchange
    "hdx_api": "https://data.humdata.org/api/3/action/package_search",
    
    # World Bank
    "worldbank_population": "https://api.worldbank.org/v2/country/{country}/indicator/SP.POP.TOTL",
    
    # GeoNames (cities)
    "geonames_cities": "http://api.geonames.org/searchJSON",
    
    # OpenStreetMap Nominatim (geocoding)
    "nominatim": "https://nominatim.openstreetmap.org/search",
    
    # REST Countries
    "restcountries": "https://restcountries.com/v3.1/all",
}

# Rate limits (requests per minute)
RATE_LIMITS = {
    "default": 30,
    "nominatim": 1,  # OSM has strict limits
    "geonames": 10,
    "statscan": 30,
    "hdx": 60,
}

# Cache TTL (seconds)
DEFAULT_CACHE_TTL = 3600 * 24  # 24 hours


# =============================================================================
# Caching
# =============================================================================

@dataclass
class CacheEntry:
    """Cached data entry."""
    data: Any
    timestamp: float
    ttl: int
    source: str


class DataCache:
    """Simple file-based cache for collected data."""
    
    def __init__(self, cache_dir: str = ".cache/grais"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.memory_cache: Dict[str, CacheEntry] = {}
    
    def _get_key(self, url: str, params: Optional[Dict] = None) -> str:
        """Generate cache key from URL and params."""
        key_str = url + json.dumps(params or {}, sort_keys=True)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, url: str, params: Optional[Dict] = None) -> Optional[Any]:
        """Get cached data if valid."""
        key = self._get_key(url, params)
        
        # Check memory cache first
        if key in self.memory_cache:
            entry = self.memory_cache[key]
            if time.time() - entry.timestamp < entry.ttl:
                logger.debug(f"Cache hit (memory): {url}")
                return entry.data
        
        # Check file cache
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    cached = json.load(f)
                if time.time() - cached["timestamp"] < cached["ttl"]:
                    logger.debug(f"Cache hit (file): {url}")
                    self.memory_cache[key] = CacheEntry(
                        data=cached["data"],
                        timestamp=cached["timestamp"],
                        ttl=cached["ttl"],
                        source=cached.get("source", "unknown"),
                    )
                    return cached["data"]
            except (json.JSONDecodeError, KeyError):
                pass
        
        return None
    
    def set(self, url: str, data: Any, params: Optional[Dict] = None, 
            ttl: int = DEFAULT_CACHE_TTL, source: str = "unknown") -> None:
        """Cache data."""
        key = self._get_key(url, params)
        timestamp = time.time()
        
        # Memory cache
        self.memory_cache[key] = CacheEntry(
            data=data, timestamp=timestamp, ttl=ttl, source=source
        )
        
        # File cache
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w") as f:
                json.dump({
                    "data": data,
                    "timestamp": timestamp,
                    "ttl": ttl,
                    "source": source,
                    "url": url,
                }, f)
        except (TypeError, IOError) as e:
            logger.warning(f"Failed to write cache: {e}")
    
    def clear(self) -> None:
        """Clear all caches."""
        self.memory_cache.clear()
        for f in self.cache_dir.glob("*.json"):
            f.unlink()


# =============================================================================
# Rate Limiting
# =============================================================================

class RateLimiter:
    """Simple rate limiter for API requests."""
    
    def __init__(self):
        self.requests: Dict[str, List[float]] = {}
    
    def wait_if_needed(self, domain: str, requests_per_minute: int = 30) -> None:
        """Wait if rate limit would be exceeded."""
        now = time.time()
        window = 60  # 1 minute window
        
        if domain not in self.requests:
            self.requests[domain] = []
        
        # Clean old requests
        self.requests[domain] = [t for t in self.requests[domain] if now - t < window]
        
        # Check if we need to wait
        if len(self.requests[domain]) >= requests_per_minute:
            oldest = min(self.requests[domain])
            wait_time = window - (now - oldest) + 0.1
            if wait_time > 0:
                logger.info(f"Rate limit: waiting {wait_time:.1f}s for {domain}")
                time.sleep(wait_time)
        
        # Record this request
        self.requests[domain].append(time.time())


# =============================================================================
# Data Collector
# =============================================================================

class DataCollector:
    """
    Automated data collector for GRAIS.
    
    Fetches real-world data from public sources with:
    - Automatic rate limiting
    - Response caching
    - Data validation
    - Multiple source fallback
    """
    
    def __init__(
        self,
        cache_dir: str = ".cache/grais",
        user_agent: str = "GRAIS-DataCollector/1.0 (humanitarian-resource-planning)",
        timeout: int = 30,
    ):
        self.cache = DataCache(cache_dir)
        self.rate_limiter = RateLimiter()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json",
        })
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        return urlparse(url).netloc
    
    def _get_rate_limit(self, url: str) -> int:
        """Get rate limit for a URL."""
        domain = self._get_domain(url)
        for key, limit in RATE_LIMITS.items():
            if key in domain:
                return limit
        return RATE_LIMITS["default"]
    
    def fetch(
        self,
        url: str,
        params: Optional[Dict] = None,
        use_cache: bool = True,
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ) -> Optional[Dict]:
        """
        Fetch data from URL with caching and rate limiting.
        
        Args:
            url: URL to fetch
            params: Query parameters
            use_cache: Whether to use caching
            cache_ttl: Cache time-to-live in seconds
        
        Returns:
            JSON response data or None on error
        """
        # Check cache
        if use_cache:
            cached = self.cache.get(url, params)
            if cached is not None:
                return cached
        
        # Rate limiting
        domain = self._get_domain(url)
        rate_limit = self._get_rate_limit(url)
        self.rate_limiter.wait_if_needed(domain, rate_limit)
        
        # Fetch
        try:
            logger.info(f"Fetching: {url}")
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            
            # Cache response
            if use_cache:
                self.cache.set(url, data, params, cache_ttl, source=domain)
            
            return data
        
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {url}: {e}")
            return None
    
    # =========================================================================
    # Country/Region Data
    # =========================================================================
    
    def collect_restcountries(self) -> List[Dict]:
        """Collect country data from REST Countries API."""
        data = self.fetch(DATA_SOURCES["restcountries"])
        if not data:
            return []
        
        countries = []
        for country in data:
            try:
                countries.append({
                    "name": country.get("name", {}).get("common", "Unknown"),
                    "population": country.get("population", 0),
                    "lat": country.get("latlng", [0, 0])[0],
                    "lon": country.get("latlng", [0, 0])[1],
                    "region": country.get("region", ""),
                    "subregion": country.get("subregion", ""),
                    "area": country.get("area", 0),
                })
            except (KeyError, IndexError, TypeError):
                continue
        
        return countries
    
    def collect_geonames_cities(
        self,
        country: str = "CA",
        max_rows: int = 50,
        min_population: int = 50000,
    ) -> List[Dict]:
        """
        Collect city data from GeoNames API.
        
        Note: Requires free GeoNames account for username.
        Set GEONAMES_USERNAME environment variable.
        """
        username = os.getenv("GEONAMES_USERNAME", "demo")
        
        params = {
            "country": country,
            "maxRows": max_rows,
            "featureClass": "P",  # Populated places
            "orderby": "population",
            "username": username,
        }
        
        if min_population:
            params["cities"] = f"cities{min_population // 1000}"
        
        data = self.fetch(DATA_SOURCES["geonames_cities"], params)
        if not data or "geonames" not in data:
            return []
        
        cities = []
        for city in data["geonames"]:
            cities.append({
                "name": city.get("name", ""),
                "population": int(city.get("population", 0)),
                "lat": float(city.get("lat", 0)),
                "lon": float(city.get("lng", 0)),
                "country": city.get("countryName", ""),
                "admin": city.get("adminName1", ""),
            })
        
        return cities
    
    def collect_worldbank_population(
        self,
        country: str = "CAN",
        year: int = 2022,
    ) -> Optional[Dict]:
        """Collect population data from World Bank API."""
        url = DATA_SOURCES["worldbank_population"].format(country=country)
        params = {"format": "json", "date": str(year)}
        
        data = self.fetch(url, params)
        if not data or len(data) < 2:
            return None
        
        # World Bank returns [metadata, data]
        records = data[1]
        if records:
            record = records[0]
            return {
                "country": record.get("country", {}).get("value", ""),
                "population": record.get("value", 0),
                "year": record.get("date", ""),
            }
        return None
    
    # =========================================================================
    # Humanitarian Data
    # =========================================================================
    
    def collect_hdx_datasets(
        self,
        query: str = "population",
        country: str = "canada",
        rows: int = 10,
    ) -> List[Dict]:
        """
        Search Humanitarian Data Exchange for datasets.
        
        HDX contains humanitarian data from UN agencies, NGOs, etc.
        """
        params = {
            "q": query,
            "fq": f'groups:"{country}"' if country else "",
            "rows": rows,
        }
        
        data = self.fetch(DATA_SOURCES["hdx_api"], params)
        if not data or "result" not in data:
            return []
        
        datasets = []
        for result in data["result"].get("results", []):
            datasets.append({
                "name": result.get("name", ""),
                "title": result.get("title", ""),
                "organization": result.get("organization", {}).get("title", ""),
                "url": f"https://data.humdata.org/dataset/{result.get('name', '')}",
                "resources": len(result.get("resources", [])),
            })
        
        return datasets
    
    # =========================================================================
    # Geocoding
    # =========================================================================
    
    def geocode(self, query: str, country: str = "") -> Optional[Tuple[float, float]]:
        """
        Geocode a location name to coordinates using Nominatim.
        
        Note: Nominatim has strict rate limits (1 req/sec).
        """
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
        }
        if country:
            params["countrycodes"] = country
        
        data = self.fetch(DATA_SOURCES["nominatim"], params, cache_ttl=86400 * 7)
        if data and len(data) > 0:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
        return None
    
    # =========================================================================
    # Auto Collection
    # =========================================================================
    
    def auto_collect_regions(
        self,
        country: str = "CA",
        min_population: int = 50000,
        max_regions: int = 20,
        config: Optional[Dict] = None,
    ) -> Tuple[List[RegionData], List[str]]:
        """
        Automatically collect region data from available sources.
        
        Tries multiple sources in order of preference:
        1. GeoNames cities
        2. REST Countries (for country-level)
        3. Fallback to built-in data
        """
        warnings = []
        regions = []
        config = config or {}
        default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
        
        # Try GeoNames first
        cities = self.collect_geonames_cities(country, max_regions, min_population)
        
        if cities:
            for city in cities[:max_regions]:
                population = city["population"]
                current_supply = {res: population * 0.01 for res in default_resources}
                consumption_rate = {res: population * 0.005 for res in default_resources}
                
                # Estimate risk based on population density (simplified)
                risk_factor = min(0.5, population / 5_000_000)
                
                regions.append(RegionData(
                    name=city["name"],
                    population=population,
                    current_supply=current_supply,
                    consumption_rate=consumption_rate,
                    risk_factor=risk_factor,
                    coordinates=(city["lat"], city["lon"]),
                ))
            
            logger.info(f"Collected {len(regions)} regions from GeoNames")
        else:
            warnings.append("GeoNames data unavailable; trying REST Countries")
            
            # Fallback: REST Countries for country-level data
            countries = self.collect_restcountries()
            if countries:
                for c in countries:
                    if c["population"] > min_population:
                        population = c["population"]
                        regions.append(RegionData(
                            name=c["name"],
                            population=population,
                            current_supply={res: population * 0.01 for res in default_resources},
                            consumption_rate={res: population * 0.005 for res in default_resources},
                            risk_factor=0.3,
                            coordinates=(c["lat"], c["lon"]),
                        ))
                        if len(regions) >= max_regions:
                            break
        
        # Final fallback: use built-in data
        if not regions:
            warnings.append("API data unavailable; using built-in fallback data")
            regions = self._get_fallback_regions(country, max_regions, config)
        
        return regions, warnings
    
    def _get_fallback_regions(
        self,
        country: str,
        max_regions: int,
        config: Optional[Dict] = None,
    ) -> List[RegionData]:
        """Built-in fallback data when APIs are unavailable."""
        config = config or {}
        default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
        
        # Built-in data for common countries
        FALLBACK_DATA = {
            "CA": [
                ("Toronto", 2930000, 43.6532, -79.3832),
                ("Montreal", 1780000, 45.5017, -73.5673),
                ("Vancouver", 675218, 49.2827, -123.1207),
                ("Calgary", 1306784, 51.0447, -114.0719),
                ("Edmonton", 1010899, 53.5461, -113.4938),
                ("Ottawa", 1017449, 45.4215, -75.6972),
                ("Winnipeg", 749534, 49.8951, -97.1384),
                ("Quebec City", 549459, 46.8139, -71.2080),
                ("Hamilton", 569353, 43.2557, -79.8711),
                ("Kitchener", 256885, 43.4516, -80.4925),
            ],
            "US": [
                ("New York", 8336817, 40.7128, -74.0060),
                ("Los Angeles", 3979576, 34.0522, -118.2437),
                ("Chicago", 2693976, 41.8781, -87.6298),
                ("Houston", 2320268, 29.7604, -95.3698),
                ("Phoenix", 1680992, 33.4484, -112.0740),
                ("Philadelphia", 1584064, 39.9526, -75.1652),
                ("San Antonio", 1547253, 29.4241, -98.4936),
                ("San Diego", 1423851, 32.7157, -117.1611),
                ("Dallas", 1343573, 32.7767, -96.7970),
                ("San Jose", 1013240, 37.3382, -121.8863),
            ],
            "GB": [
                ("London", 8982000, 51.5074, -0.1278),
                ("Birmingham", 1141816, 52.4862, -1.8904),
                ("Manchester", 547627, 53.4808, -2.2426),
                ("Leeds", 474632, 53.8008, -1.5491),
                ("Glasgow", 626410, 55.8642, -4.2518),
                ("Liverpool", 496784, 53.4084, -2.9916),
                ("Newcastle", 302820, 54.9783, -1.6178),
                ("Sheffield", 584853, 53.3811, -1.4701),
            ],
        }
        
        # Default to CA if country not found
        data = FALLBACK_DATA.get(country.upper(), FALLBACK_DATA.get("CA", []))
        
        regions = []
        for name, population, lat, lon in data[:max_regions]:
            current_supply = {res: population * 0.01 for res in default_resources}
            consumption_rate = {res: population * 0.005 for res in default_resources}
            risk_factor = min(0.5, population / 5_000_000)
            
            regions.append(RegionData(
                name=name,
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=risk_factor,
                coordinates=(lat, lon),
            ))
        
        return regions
    
    def auto_collect_depots(
        self,
        regions: List[RegionData],
        depot_ratio: float = 0.25,
        config: Optional[Dict] = None,
    ) -> Tuple[List[Depot], List[str]]:
        """
        Auto-generate depot locations based on regions.
        
        Places depots near largest population centers.
        """
        warnings = []
        config = config or {}
        default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
        
        # Sort by population, take top N for depot locations
        sorted_regions = sorted(regions, key=lambda r: r.population, reverse=True)
        num_depots = max(1, int(len(regions) * depot_ratio))
        
        depots = []
        for i, region in enumerate(sorted_regions[:num_depots]):
            # Offset depot slightly from region center
            lat_offset = 0.1 * (1 if i % 2 == 0 else -1)
            lon_offset = 0.1 * (1 if i % 3 == 0 else -1)
            
            # Stock based on nearby population
            total_nearby_pop = sum(r.population for r in regions) / num_depots
            stock = {res: total_nearby_pop * 0.02 for res in default_resources}
            
            depots.append(Depot(
                name=f"Depot-{region.name}",
                stock=stock,
                coordinates=(
                    region.coordinates[0] + lat_offset,
                    region.coordinates[1] + lon_offset,
                ),
            ))
        
        logger.info(f"Generated {len(depots)} depots")
        return depots, warnings
    
    def auto_collect(
        self,
        country: str = "CA",
        min_population: int = 50000,
        max_regions: int = 20,
        config: Optional[Dict] = None,
    ) -> Tuple[List[RegionData], List[Depot], List[str]]:
        """
        Automatically collect both regions and depots.
        
        Returns:
            Tuple of (regions, depots, warnings)
        """
        all_warnings = []
        
        regions, r_warnings = self.auto_collect_regions(
            country, min_population, max_regions, config
        )
        all_warnings.extend(r_warnings)
        
        depots, d_warnings = self.auto_collect_depots(regions, config=config)
        all_warnings.extend(d_warnings)
        
        return regions, depots, all_warnings
    
    # =========================================================================
    # Data Export
    # =========================================================================
    
    def export_to_json(
        self,
        regions: List[RegionData],
        depots: List[Depot],
        filepath: str,
    ) -> None:
        """Export collected data to JSON file."""
        data = {
            "regions": [
                {
                    "name": r.name,
                    "population": r.population,
                    "lat": r.coordinates[0],
                    "lon": r.coordinates[1],
                    "risk_factor": r.risk_factor,
                    "current_supply": r.current_supply,
                    "consumption_rate": r.consumption_rate,
                }
                for r in regions
            ],
            "depots": [
                {
                    "name": d.name,
                    "lat": d.coordinates[0],
                    "lon": d.coordinates[1],
                    "stock": d.stock,
                }
                for d in depots
            ],
            "metadata": {
                "collected_at": datetime.utcnow().isoformat(),
                "source": "GRAIS DataCollector",
            },
        }
        
        Path(filepath).write_text(json.dumps(data, indent=2))
        logger.info(f"Exported data to {filepath}")


# =============================================================================
# Convenience Functions
# =============================================================================

def collect_and_save(
    country: str = "CA",
    output_file: str = "collected_data.json",
    min_population: int = 50000,
    max_regions: int = 20,
) -> Tuple[List[RegionData], List[Depot]]:
    """
    Convenience function to collect data and save to file.
    
    Example:
        regions, depots = collect_and_save("CA", "canada_data.json")
    """
    collector = DataCollector()
    regions, depots, warnings = collector.auto_collect(
        country=country,
        min_population=min_population,
        max_regions=max_regions,
    )
    
    if warnings:
        for w in warnings:
            logger.warning(w)
    
    collector.export_to_json(regions, depots, output_file)
    return regions, depots


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="GRAIS Data Collector")
    parser.add_argument("--country", default="CA", help="Country code (e.g., CA, US, GB)")
    parser.add_argument("--output", default="collected_data.json", help="Output file")
    parser.add_argument("--max-regions", type=int, default=20, help="Max regions to collect")
    parser.add_argument("--min-population", type=int, default=50000, help="Min population filter")
    
    args = parser.parse_args()
    
    print(f"Collecting data for {args.country}...")
    regions, depots = collect_and_save(
        args.country,
        args.output,
        args.min_population,
        args.max_regions,
    )
    print(f"Collected {len(regions)} regions and {len(depots)} depots")
    print(f"Saved to {args.output}")
