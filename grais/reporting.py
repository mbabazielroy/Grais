"""
Enterprise Reporting Module for GRAIS

Generates professional reports for government stakeholders:
- PDF reports with charts and maps
- Excel exports with multiple sheets
- GeoJSON for GIS integration
- CSV exports
- Executive summaries

Designed for government procurement requirements.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("grais.reporting")

# Optional imports for advanced features
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    pd = None


# =============================================================================
# Report Data Models
# =============================================================================

@dataclass
class ReportMetadata:
    """Report metadata."""
    title: str
    generated_at: datetime
    generated_by: str
    organization: str
    classification: str = "UNCLASSIFIED"  # Government classification
    version: str = "1.0"
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


@dataclass 
class ReportSection:
    """Report section."""
    title: str
    content: Any
    section_type: str  # "table", "chart", "map", "text", "summary"


# =============================================================================
# CSV Export
# =============================================================================

def export_predictions_csv(predictions: List[Dict], output: Union[str, io.StringIO]) -> None:
    """Export predictions to CSV."""
    if not predictions:
        return
    
    fieldnames = [
        "region", "population", "shortage_probability", "shortage_severity",
        "expected_shortage_day", "probability_lower", "probability_upper",
        "day_lower", "day_upper", "risk_factor", "latitude", "longitude"
    ]
    
    rows = []
    for p in predictions:
        rows.append({
            "region": p.get("region", ""),
            "population": p.get("population", 0),
            "shortage_probability": p.get("shortageProbability", 0),
            "shortage_severity": p.get("shortageSeverity", ""),
            "expected_shortage_day": p.get("expectedShortageDay", 0),
            "probability_lower": p.get("shortageProbLower", 0),
            "probability_upper": p.get("shortageProbUpper", 0),
            "day_lower": p.get("expectedDayLower", 0),
            "day_upper": p.get("expectedDayUpper", 0),
            "risk_factor": p.get("inputs", {}).get("risk_factor", 0),
            "latitude": p.get("coordinates", {}).get("lat", 0),
            "longitude": p.get("coordinates", {}).get("lon", 0),
        })
    
    if isinstance(output, str):
        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_allocations_csv(allocation: Dict, output: Union[str, io.StringIO]) -> None:
    """Export allocation shipments to CSV."""
    shipments = allocation.get("shipments", [])
    
    fieldnames = ["from_depot", "to_region", "resource", "quantity", "distance_km"]
    
    rows = []
    for s in shipments:
        rows.append({
            "from_depot": s.get("from", ""),
            "to_region": s.get("to", ""),
            "resource": s.get("resource", "mixed"),
            "quantity": s.get("quantity", 0),
            "distance_km": s.get("distanceKm", 0),
        })
    
    if isinstance(output, str):
        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_priorities_csv(priorities: List[Dict], output: Union[str, io.StringIO]) -> None:
    """Export priorities to CSV."""
    fieldnames = ["rank", "region", "priority_score", "urgency_category", 
                  "recommended_action", "explanation"]
    
    rows = []
    for i, p in enumerate(priorities, 1):
        rows.append({
            "rank": i,
            "region": p.get("regionName", ""),
            "priority_score": p.get("priorityScore", 0),
            "urgency_category": p.get("urgencyCategory", ""),
            "recommended_action": p.get("recommendedAction", ""),
            "explanation": p.get("explanation", ""),
        })
    
    if isinstance(output, str):
        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# Excel Export (requires pandas + openpyxl)
# =============================================================================

def export_full_report_excel(
    result: Dict,
    output: str,
    metadata: Optional[ReportMetadata] = None,
) -> None:
    """
    Export full pipeline result to Excel workbook.
    
    Sheets:
    - Summary
    - Predictions
    - Allocations
    - Priorities
    - Unmet Demand
    - Configuration
    """
    if not HAS_PANDAS:
        raise ImportError("pandas and openpyxl required for Excel export")
    
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Summary sheet
        summary_data = {
            "Metric": [
                "Report Generated",
                "Total Regions",
                "Total Shipments",
                "Solver Status",
                "Objective Value",
                "Total Unmet Demand",
                "Regions with Unmet Demand",
                "Data Source",
            ],
            "Value": [
                datetime.utcnow().isoformat(),
                len(result.get("predictions", [])),
                len(result.get("allocation", {}).get("shipments", [])),
                result.get("allocation", {}).get("solverStatus", ""),
                result.get("allocation", {}).get("objective", 0),
                sum(u.get("unmet", 0) for u in result.get("allocation", {}).get("unmet", [])),
                len(result.get("allocation", {}).get("unmet", [])),
                result.get("metadata", {}).get("data_source", "unknown"),
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)
        
        # Predictions sheet
        predictions = result.get("predictions", [])
        if predictions:
            pred_df = pd.DataFrame([
                {
                    "Region": p.get("region"),
                    "Population": p.get("population"),
                    "Shortage Probability": p.get("shortageProbability"),
                    "Severity": p.get("shortageSeverity"),
                    "Expected Day": p.get("expectedShortageDay"),
                    "Prob Lower": p.get("shortageProbLower"),
                    "Prob Upper": p.get("shortageProbUpper"),
                    "Latitude": p.get("coordinates", {}).get("lat"),
                    "Longitude": p.get("coordinates", {}).get("lon"),
                }
                for p in predictions
            ])
            pred_df.to_excel(writer, sheet_name="Predictions", index=False)
        
        # Allocations sheet
        shipments = result.get("allocation", {}).get("shipments", [])
        if shipments:
            alloc_df = pd.DataFrame([
                {
                    "From Depot": s.get("from"),
                    "To Region": s.get("to"),
                    "Resource": s.get("resource", "mixed"),
                    "Quantity": s.get("quantity"),
                    "Distance (km)": s.get("distanceKm"),
                }
                for s in shipments
            ])
            alloc_df.to_excel(writer, sheet_name="Allocations", index=False)
        
        # Priorities sheet
        priorities = result.get("priorities", [])
        if priorities:
            prio_df = pd.DataFrame([
                {
                    "Rank": i + 1,
                    "Region": p.get("regionName"),
                    "Priority Score": p.get("priorityScore"),
                    "Urgency": p.get("urgencyCategory"),
                    "Recommended Action": p.get("recommendedAction"),
                    "Explanation": p.get("explanation"),
                }
                for i, p in enumerate(priorities)
            ])
            prio_df.to_excel(writer, sheet_name="Priorities", index=False)
        
        # Unmet Demand sheet
        unmet = result.get("allocation", {}).get("unmet", [])
        if unmet:
            unmet_df = pd.DataFrame([
                {
                    "Region": u.get("region"),
                    "Unmet Demand": u.get("unmet"),
                }
                for u in unmet
            ])
            unmet_df.to_excel(writer, sheet_name="Unmet Demand", index=False)
        
        # Configuration sheet
        config = result.get("config", {})
        config_data = {
            "Setting": list(config.keys()),
            "Value": [str(v) for v in config.values()],
        }
        pd.DataFrame(config_data).to_excel(writer, sheet_name="Configuration", index=False)
    
    logger.info(f"Excel report exported to {output}")


# =============================================================================
# GeoJSON Export (for GIS integration)
# =============================================================================

def export_geojson(
    predictions: List[Dict],
    priorities: Optional[List[Dict]] = None,
    output: Optional[str] = None,
) -> Dict:
    """
    Export predictions as GeoJSON for GIS integration.
    
    Compatible with:
    - ESRI ArcGIS
    - QGIS
    - MapBox
    - Leaflet
    """
    # Create priority lookup
    priority_map = {}
    if priorities:
        priority_map = {p["regionName"]: p for p in priorities}
    
    features = []
    for pred in predictions:
        coords = pred.get("coordinates", {})
        lat = coords.get("lat", 0)
        lon = coords.get("lon", 0)
        
        priority = priority_map.get(pred.get("region"), {})
        
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]  # GeoJSON uses [lon, lat]
            },
            "properties": {
                "name": pred.get("region", ""),
                "population": pred.get("population", 0),
                "shortage_probability": pred.get("shortageProbability", 0),
                "shortage_severity": pred.get("shortageSeverity", ""),
                "expected_shortage_day": pred.get("expectedShortageDay", 0),
                "priority_score": priority.get("priorityScore", 0),
                "urgency_category": priority.get("urgencyCategory", ""),
                "recommended_action": priority.get("recommendedAction", ""),
            }
        }
        features.append(feature)
    
    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "generated_at": datetime.utcnow().isoformat(),
            "crs": "EPSG:4326",
            "source": "GRAIS",
        }
    }
    
    if output:
        with open(output, "w") as f:
            json.dump(geojson, f, indent=2)
        logger.info(f"GeoJSON exported to {output}")
    
    return geojson


# =============================================================================
# Executive Summary Generator
# =============================================================================

def generate_executive_summary(result: Dict) -> str:
    """
    Generate executive summary text for decision-makers.
    
    Returns markdown-formatted summary.
    """
    predictions = result.get("predictions", [])
    allocation = result.get("allocation", {})
    priorities = result.get("priorities", [])
    config = result.get("config", {})
    
    # Calculate statistics
    total_regions = len(predictions)
    high_risk = sum(1 for p in predictions if p.get("shortageSeverity") == "high")
    medium_risk = sum(1 for p in predictions if p.get("shortageSeverity") == "medium")
    total_shipments = len(allocation.get("shipments", []))
    total_unmet = sum(u.get("unmet", 0) for u in allocation.get("unmet", []))
    regions_with_unmet = len(allocation.get("unmet", []))
    
    # Get top priorities
    top_priorities = priorities[:5] if priorities else []
    
    summary = f"""# GRAIS Executive Summary

**Generated:** {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}  
**Region:** {config.get("region", "Unknown")}  
**Time Horizon:** {config.get("timeHorizonDays", 7)} days  

## Overview

This report analyzes resource allocation needs for **{total_regions} regions** based on 
predicted shortage risks and available supply capacity.

## Key Findings

### Risk Assessment
- **High Risk Regions:** {high_risk}
- **Medium Risk Regions:** {medium_risk}
- **Low Risk Regions:** {total_regions - high_risk - medium_risk}

### Resource Allocation
- **Total Shipments Planned:** {total_shipments}
- **Optimization Status:** {allocation.get("solverStatus", "Unknown")}
- **Regions with Unmet Demand:** {regions_with_unmet}
- **Total Unmet Demand:** {total_unmet:,.0f} units

## Priority Regions

The following regions require immediate attention:

| Rank | Region | Priority Score | Urgency | Recommended Action |
|------|--------|----------------|---------|-------------------|
"""
    
    for i, p in enumerate(top_priorities, 1):
        summary += f"| {i} | {p.get('regionName', '')} | {p.get('priorityScore', 0):.3f} | {p.get('urgencyCategory', '').upper()} | {p.get('recommendedAction', '')} |\n"
    
    summary += f"""
## Recommendations

1. **Immediate Action:** Focus resources on {top_priorities[0]['regionName'] if top_priorities else 'N/A'}
2. **Monitoring:** Track regions with medium urgency for escalation
3. **Contingency:** Pre-position supplies for potential demand surges

## Data Quality

- **Data Source:** {result.get("metadata", {}).get("data_source", "Unknown")}
- **Prediction Model:** {result.get("metadata", {}).get("prediction_model", "Unknown")}
- **Capabilities:** {result.get("metadata", {}).get("capabilities", {})}

---
*This report was automatically generated by GRAIS (Global Resource Allocation Intelligence System)*
"""
    
    return summary


# =============================================================================
# Report Generator Class
# =============================================================================

class ReportGenerator:
    """
    Comprehensive report generator for government stakeholders.
    
    Usage:
        generator = ReportGenerator(result)
        generator.export_excel("report.xlsx")
        generator.export_geojson("map.geojson")
        summary = generator.generate_summary()
    """
    
    def __init__(self, result: Dict, metadata: Optional[ReportMetadata] = None):
        self.result = result
        self.metadata = metadata or ReportMetadata(
            title="GRAIS Resource Allocation Report",
            generated_at=datetime.utcnow(),
            generated_by="GRAIS System",
            organization="Unknown",
        )
    
    def export_csv_bundle(self, output_dir: str) -> List[str]:
        """Export all data as CSV files."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        files = []
        
        # Predictions
        pred_file = output_dir / "predictions.csv"
        export_predictions_csv(self.result.get("predictions", []), str(pred_file))
        files.append(str(pred_file))
        
        # Allocations
        alloc_file = output_dir / "allocations.csv"
        export_allocations_csv(self.result.get("allocation", {}), str(alloc_file))
        files.append(str(alloc_file))
        
        # Priorities
        prio_file = output_dir / "priorities.csv"
        export_priorities_csv(self.result.get("priorities", []), str(prio_file))
        files.append(str(prio_file))
        
        logger.info(f"CSV bundle exported to {output_dir}")
        return files
    
    def export_excel(self, output: str) -> None:
        """Export full report as Excel workbook."""
        export_full_report_excel(self.result, output, self.metadata)
    
    def export_geojson(self, output: str) -> Dict:
        """Export as GeoJSON for GIS."""
        return export_geojson(
            self.result.get("predictions", []),
            self.result.get("priorities", []),
            output,
        )
    
    def generate_summary(self) -> str:
        """Generate executive summary."""
        return generate_executive_summary(self.result)
    
    def export_all(self, output_dir: str) -> Dict[str, str]:
        """Export all formats."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        outputs = {}
        
        # CSV bundle
        csv_files = self.export_csv_bundle(output_dir / "csv")
        outputs["csv"] = str(output_dir / "csv")
        
        # Excel
        if HAS_PANDAS:
            excel_file = output_dir / "report.xlsx"
            self.export_excel(str(excel_file))
            outputs["excel"] = str(excel_file)
        
        # GeoJSON
        geojson_file = output_dir / "map.geojson"
        self.export_geojson(str(geojson_file))
        outputs["geojson"] = str(geojson_file)
        
        # Summary
        summary_file = output_dir / "executive_summary.md"
        summary_file.write_text(self.generate_summary())
        outputs["summary"] = str(summary_file)
        
        logger.info(f"All reports exported to {output_dir}")
        return outputs
