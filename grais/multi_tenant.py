"""
Multi-Tenancy Support for GRAIS

Enables deployment for multiple government agencies/organizations
with proper data isolation, configuration management, and resource quotas.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("grais.multi_tenant")


# =============================================================================
# Tenant Models
# =============================================================================

@dataclass
class TenantQuota:
    """Resource quotas per tenant."""
    max_api_calls_per_hour: int = 10000
    max_api_calls_per_day: int = 100000
    max_regions: int = 1000
    max_depots: int = 500
    max_storage_gb: float = 10.0
    max_users: int = 100
    max_concurrent_requests: int = 50


@dataclass
class TenantConfig:
    """Tenant-specific configuration."""
    default_region: str = "world"
    time_horizon_days: int = 7
    prediction_model: str = "calibrated"
    optimization_solver: str = "ortools"
    enable_forecasting: bool = True
    enable_llm_prioritization: bool = False
    custom_settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Tenant:
    """
    Tenant representation.
    
    Each tenant represents a separate government agency or organization
    with isolated data and configuration.
    """
    tenant_id: str
    name: str
    organization_type: str  # "federal", "state", "local", "ngo", "international"
    country: str
    region: Optional[str] = None
    
    # Settings
    config: TenantConfig = field(default_factory=TenantConfig)
    quota: TenantQuota = field(default_factory=TenantQuota)
    
    # Status
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # Contact
    admin_email: str = ""
    admin_phone: str = ""
    
    # Features
    enabled_features: Set[str] = field(default_factory=lambda: {
        "predictions", "allocations", "priorities", "reports"
    })
    
    # Branding
    branding: Dict[str, str] = field(default_factory=dict)
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Tenant Manager
# =============================================================================

class TenantManager:
    """
    Manages tenants and provides tenant-scoped operations.
    
    In production, this should use a database for persistence.
    """
    
    def __init__(self, storage_path: str = "data/tenants"):
        self.storage_path = Path(storage_path)
        self.tenants: Dict[str, Tenant] = {}
        self._load_tenants()
    
    def _load_tenants(self) -> None:
        """Load tenants from storage."""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        for tenant_file in self.storage_path.glob("*.json"):
            try:
                with open(tenant_file) as f:
                    data = json.load(f)
                    tenant = self._dict_to_tenant(data)
                    self.tenants[tenant.tenant_id] = tenant
                    logger.debug(f"Loaded tenant: {tenant.name}")
            except Exception as e:
                logger.error(f"Failed to load tenant from {tenant_file}: {e}")
        
        # Create default tenant if none exist
        if not self.tenants:
            self._create_default_tenant()
    
    def _dict_to_tenant(self, data: Dict) -> Tenant:
        """Convert dictionary to Tenant object."""
        config_data = data.pop("config", {})
        quota_data = data.pop("quota", {})
        
        return Tenant(
            tenant_id=data.get("tenant_id", ""),
            name=data.get("name", ""),
            organization_type=data.get("organization_type", "local"),
            country=data.get("country", ""),
            region=data.get("region"),
            config=TenantConfig(**config_data),
            quota=TenantQuota(**quota_data),
            is_active=data.get("is_active", True),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.utcnow(),
            admin_email=data.get("admin_email", ""),
            admin_phone=data.get("admin_phone", ""),
            enabled_features=set(data.get("enabled_features", ["predictions", "allocations", "priorities", "reports"])),
            branding=data.get("branding", {}),
            metadata=data.get("metadata", {}),
        )
    
    def _tenant_to_dict(self, tenant: Tenant) -> Dict:
        """Convert Tenant object to dictionary."""
        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "organization_type": tenant.organization_type,
            "country": tenant.country,
            "region": tenant.region,
            "config": {
                "default_region": tenant.config.default_region,
                "time_horizon_days": tenant.config.time_horizon_days,
                "prediction_model": tenant.config.prediction_model,
                "optimization_solver": tenant.config.optimization_solver,
                "enable_forecasting": tenant.config.enable_forecasting,
                "enable_llm_prioritization": tenant.config.enable_llm_prioritization,
                "custom_settings": tenant.config.custom_settings,
            },
            "quota": {
                "max_api_calls_per_hour": tenant.quota.max_api_calls_per_hour,
                "max_api_calls_per_day": tenant.quota.max_api_calls_per_day,
                "max_regions": tenant.quota.max_regions,
                "max_depots": tenant.quota.max_depots,
                "max_storage_gb": tenant.quota.max_storage_gb,
                "max_users": tenant.quota.max_users,
                "max_concurrent_requests": tenant.quota.max_concurrent_requests,
            },
            "is_active": tenant.is_active,
            "created_at": tenant.created_at.isoformat(),
            "updated_at": tenant.updated_at.isoformat(),
            "admin_email": tenant.admin_email,
            "admin_phone": tenant.admin_phone,
            "enabled_features": list(tenant.enabled_features),
            "branding": tenant.branding,
            "metadata": tenant.metadata,
        }
    
    def _save_tenant(self, tenant: Tenant) -> None:
        """Save tenant to storage."""
        tenant_file = self.storage_path / f"{tenant.tenant_id}.json"
        with open(tenant_file, "w") as f:
            json.dump(self._tenant_to_dict(tenant), f, indent=2)
    
    def _create_default_tenant(self) -> Tenant:
        """Create default system tenant."""
        tenant = Tenant(
            tenant_id="default",
            name="Default Tenant",
            organization_type="local",
            country="Global",
            admin_email="admin@grais.example.com",
        )
        self.tenants[tenant.tenant_id] = tenant
        self._save_tenant(tenant)
        logger.info("Created default tenant")
        return tenant
    
    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        organization_type: str,
        country: str,
        region: Optional[str] = None,
        admin_email: str = "",
        config: Optional[Dict] = None,
        quota: Optional[Dict] = None,
    ) -> Tenant:
        """Create a new tenant."""
        if tenant_id in self.tenants:
            raise ValueError(f"Tenant {tenant_id} already exists")
        
        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            organization_type=organization_type,
            country=country,
            region=region,
            admin_email=admin_email,
            config=TenantConfig(**(config or {})),
            quota=TenantQuota(**(quota or {})),
        )
        
        self.tenants[tenant_id] = tenant
        self._save_tenant(tenant)
        
        logger.info(f"Created tenant: {name} ({tenant_id})")
        return tenant
    
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Get tenant by ID."""
        return self.tenants.get(tenant_id)
    
    def update_tenant(self, tenant_id: str, **updates) -> Optional[Tenant]:
        """Update tenant properties."""
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return None
        
        for key, value in updates.items():
            if hasattr(tenant, key):
                setattr(tenant, key, value)
        
        tenant.updated_at = datetime.utcnow()
        self._save_tenant(tenant)
        
        logger.info(f"Updated tenant: {tenant_id}")
        return tenant
    
    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant (soft delete)."""
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return False
        
        tenant.is_active = False
        tenant.updated_at = datetime.utcnow()
        self._save_tenant(tenant)
        
        logger.info(f"Deactivated tenant: {tenant_id}")
        return True
    
    def list_tenants(self, active_only: bool = True) -> List[Tenant]:
        """List all tenants."""
        tenants = list(self.tenants.values())
        if active_only:
            tenants = [t for t in tenants if t.is_active]
        return sorted(tenants, key=lambda t: t.name)
    
    def get_tenant_data_path(self, tenant_id: str) -> Path:
        """Get tenant-specific data storage path."""
        path = Path(f"data/tenant_data/{tenant_id}")
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def check_quota(self, tenant_id: str, resource: str, amount: int = 1) -> bool:
        """Check if tenant has quota for an operation."""
        tenant = self.tenants.get(tenant_id)
        if not tenant or not tenant.is_active:
            return False
        
        # In production, this would check actual usage
        return True


# Global tenant manager
tenant_manager = TenantManager()


# =============================================================================
# Tenant-Scoped Data Access
# =============================================================================

class TenantDataStore:
    """
    Provides tenant-scoped data access.
    
    Ensures data isolation between tenants.
    """
    
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.base_path = tenant_manager.get_tenant_data_path(tenant_id)
    
    def get_regions_path(self) -> Path:
        """Get path for tenant's regions data."""
        return self.base_path / "regions.json"
    
    def get_depots_path(self) -> Path:
        """Get path for tenant's depots data."""
        return self.base_path / "depots.json"
    
    def get_reports_path(self) -> Path:
        """Get path for tenant's reports."""
        path = self.base_path / "reports"
        path.mkdir(exist_ok=True)
        return path
    
    def get_audit_path(self) -> Path:
        """Get path for tenant's audit logs."""
        path = self.base_path / "audit"
        path.mkdir(exist_ok=True)
        return path
    
    def save_regions(self, regions: List[Dict]) -> None:
        """Save regions data for tenant."""
        with open(self.get_regions_path(), "w") as f:
            json.dump(regions, f, indent=2)
    
    def load_regions(self) -> Optional[List[Dict]]:
        """Load regions data for tenant."""
        path = self.get_regions_path()
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)
    
    def save_depots(self, depots: List[Dict]) -> None:
        """Save depots data for tenant."""
        with open(self.get_depots_path(), "w") as f:
            json.dump(depots, f, indent=2)
    
    def load_depots(self) -> Optional[List[Dict]]:
        """Load depots data for tenant."""
        path = self.get_depots_path()
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)
