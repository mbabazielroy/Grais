"""
Enterprise Security Module for GRAIS

Provides:
- API Key Authentication
- Role-Based Access Control (RBAC)
- Comprehensive Audit Logging
- Session Management

For government/enterprise deployments.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

logger = logging.getLogger("grais.security")


# =============================================================================
# Roles & Permissions
# =============================================================================

class Permission(str, Enum):
    """System permissions."""
    # Read permissions
    READ_PREDICTIONS = "read:predictions"
    READ_ALLOCATIONS = "read:allocations"
    READ_PRIORITIES = "read:priorities"
    READ_METRICS = "read:metrics"
    READ_CONFIG = "read:config"
    READ_DATA = "read:data"
    
    # Write permissions
    WRITE_CONFIG = "write:config"
    WRITE_DATA = "write:data"
    RUN_SCENARIOS = "run:scenarios"
    
    # Admin permissions
    ADMIN_USERS = "admin:users"
    ADMIN_API_KEYS = "admin:api_keys"
    ADMIN_AUDIT = "admin:audit"
    ADMIN_SYSTEM = "admin:system"


class Role(str, Enum):
    """Predefined roles."""
    VIEWER = "viewer"           # Read-only access
    ANALYST = "analyst"         # Read + run scenarios
    OPERATOR = "operator"       # Full operational access
    ADMIN = "admin"             # Full system access
    SYSTEM = "system"           # Internal system access


# Role -> Permissions mapping
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.VIEWER: {
        Permission.READ_PREDICTIONS,
        Permission.READ_ALLOCATIONS,
        Permission.READ_PRIORITIES,
        Permission.READ_DATA,
    },
    Role.ANALYST: {
        Permission.READ_PREDICTIONS,
        Permission.READ_ALLOCATIONS,
        Permission.READ_PRIORITIES,
        Permission.READ_METRICS,
        Permission.READ_CONFIG,
        Permission.READ_DATA,
        Permission.RUN_SCENARIOS,
    },
    Role.OPERATOR: {
        Permission.READ_PREDICTIONS,
        Permission.READ_ALLOCATIONS,
        Permission.READ_PRIORITIES,
        Permission.READ_METRICS,
        Permission.READ_CONFIG,
        Permission.READ_DATA,
        Permission.WRITE_DATA,
        Permission.RUN_SCENARIOS,
    },
    Role.ADMIN: {p for p in Permission},  # All permissions
    Role.SYSTEM: {p for p in Permission},  # All permissions
}


# =============================================================================
# API Key Management
# =============================================================================

@dataclass
class APIKey:
    """API Key representation."""
    key_id: str
    key_hash: str  # Store hash, not plaintext
    name: str
    role: Role
    organization: str
    created_at: datetime
    expires_at: Optional[datetime]
    is_active: bool = True
    rate_limit: int = 1000  # Requests per hour
    allowed_ips: List[str] = field(default_factory=list)  # Empty = all IPs allowed
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_used: Optional[datetime] = None
    usage_count: int = 0


class APIKeyManager:
    """
    Manages API keys for authentication.
    
    In production, this should use a database.
    """
    
    def __init__(self):
        self.keys: Dict[str, APIKey] = {}
        self._load_from_env()
    
    def _load_from_env(self) -> None:
        """Load API keys from environment."""
        # Master admin key from environment
        master_key = os.getenv("GRAIS_MASTER_API_KEY")
        if master_key:
            self.register_key(
                key=master_key,
                name="Master Admin Key",
                role=Role.ADMIN,
                organization="System",
            )
        
        # Demo key for development
        if os.getenv("ENVIRONMENT", "development") == "development":
            self.register_key(
                key="dev-api-key-for-testing",
                name="Development Key",
                role=Role.ANALYST,
                organization="Development",
            )
    
    def generate_key(self) -> str:
        """Generate a new API key."""
        return f"grais_{secrets.token_urlsafe(32)}"
    
    def _hash_key(self, key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()
    
    def register_key(
        self,
        key: str,
        name: str,
        role: Role,
        organization: str,
        expires_in_days: Optional[int] = None,
        rate_limit: int = 1000,
        allowed_ips: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> APIKey:
        """Register a new API key."""
        key_hash = self._hash_key(key)
        key_id = key_hash[:16]  # Use first 16 chars as ID
        
        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            role=role,
            organization=organization,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
            rate_limit=rate_limit,
            allowed_ips=allowed_ips or [],
            metadata=metadata or {},
        )
        
        self.keys[key_hash] = api_key
        logger.info(f"Registered API key: {name} ({key_id}) for {organization}")
        return api_key
    
    def validate_key(self, key: str, client_ip: Optional[str] = None) -> Optional[APIKey]:
        """Validate an API key and return the key object if valid."""
        key_hash = self._hash_key(key)
        api_key = self.keys.get(key_hash)
        
        if not api_key:
            return None
        
        if not api_key.is_active:
            return None
        
        if api_key.expires_at and datetime.utcnow() > api_key.expires_at:
            return None
        
        if api_key.allowed_ips and client_ip and client_ip not in api_key.allowed_ips:
            logger.warning(f"API key {api_key.key_id} used from unauthorized IP: {client_ip}")
            return None
        
        # Update usage stats
        api_key.last_used = datetime.utcnow()
        api_key.usage_count += 1
        
        return api_key
    
    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        for key_hash, api_key in self.keys.items():
            if api_key.key_id == key_id:
                api_key.is_active = False
                logger.info(f"Revoked API key: {api_key.name} ({key_id})")
                return True
        return False
    
    def list_keys(self, organization: Optional[str] = None) -> List[Dict]:
        """List API keys (without hashes)."""
        keys = []
        for api_key in self.keys.values():
            if organization and api_key.organization != organization:
                continue
            keys.append({
                "key_id": api_key.key_id,
                "name": api_key.name,
                "role": api_key.role.value,
                "organization": api_key.organization,
                "created_at": api_key.created_at.isoformat(),
                "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
                "is_active": api_key.is_active,
                "last_used": api_key.last_used.isoformat() if api_key.last_used else None,
                "usage_count": api_key.usage_count,
            })
        return keys


# Global API key manager
api_key_manager = APIKeyManager()


# =============================================================================
# Audit Logging
# =============================================================================

@dataclass
class AuditLogEntry:
    """Audit log entry."""
    timestamp: datetime
    event_type: str
    action: str
    user_id: str
    organization: str
    resource: str
    resource_id: Optional[str]
    request_id: str
    client_ip: str
    user_agent: str
    request_path: str
    request_method: str
    request_body: Optional[Dict]
    response_status: int
    response_time_ms: float
    success: bool
    error_message: Optional[str]
    metadata: Dict[str, Any]


class AuditLogger:
    """
    Comprehensive audit logging for compliance.
    
    In production, this should write to:
    - Immutable log storage (S3, Azure Blob, etc.)
    - SIEM system
    - Database for querying
    """
    
    def __init__(self, storage_path: str = "logs/audit"):
        self.storage_path = storage_path
        self.entries: List[AuditLogEntry] = []
        self._ensure_storage()
    
    def _ensure_storage(self) -> None:
        """Ensure log storage exists."""
        import os
        os.makedirs(self.storage_path, exist_ok=True)
    
    def log(
        self,
        event_type: str,
        action: str,
        user_id: str,
        organization: str,
        resource: str,
        request: Request,
        response_status: int,
        response_time_ms: float,
        success: bool,
        resource_id: Optional[str] = None,
        request_body: Optional[Dict] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> AuditLogEntry:
        """Log an audit event."""
        entry = AuditLogEntry(
            timestamp=datetime.utcnow(),
            event_type=event_type,
            action=action,
            user_id=user_id,
            organization=organization,
            resource=resource,
            resource_id=resource_id,
            request_id=getattr(request.state, "request_id", "unknown"),
            client_ip=self._get_client_ip(request),
            user_agent=request.headers.get("user-agent", "unknown"),
            request_path=str(request.url.path),
            request_method=request.method,
            request_body=self._sanitize_body(request_body),
            response_status=response_status,
            response_time_ms=response_time_ms,
            success=success,
            error_message=error_message,
            metadata=metadata or {},
        )
        
        self.entries.append(entry)
        self._write_entry(entry)
        
        # Log to standard logger as well
        log_level = logging.INFO if success else logging.WARNING
        logger.log(
            log_level,
            f"AUDIT: {event_type}.{action} by {user_id}@{organization} on {resource}",
            extra={
                "audit": True,
                "event_type": event_type,
                "action": action,
                "user_id": user_id,
                "success": success,
            }
        )
        
        return entry
    
    def _get_client_ip(self, request: Request) -> str:
        """Get client IP from request."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
    
    def _sanitize_body(self, body: Optional[Dict]) -> Optional[Dict]:
        """Remove sensitive data from request body."""
        if not body:
            return None
        
        sensitive_keys = {"password", "secret", "token", "api_key", "authorization"}
        sanitized = {}
        
        for key, value in body.items():
            if key.lower() in sensitive_keys:
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_body(value)
            else:
                sanitized[key] = value
        
        return sanitized
    
    def _write_entry(self, entry: AuditLogEntry) -> None:
        """Write entry to storage."""
        # Daily log files
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        log_file = f"{self.storage_path}/audit_{date_str}.jsonl"
        
        with open(log_file, "a") as f:
            log_data = {
                "timestamp": entry.timestamp.isoformat(),
                "event_type": entry.event_type,
                "action": entry.action,
                "user_id": entry.user_id,
                "organization": entry.organization,
                "resource": entry.resource,
                "resource_id": entry.resource_id,
                "request_id": entry.request_id,
                "client_ip": entry.client_ip,
                "user_agent": entry.user_agent,
                "request_path": entry.request_path,
                "request_method": entry.request_method,
                "response_status": entry.response_status,
                "response_time_ms": entry.response_time_ms,
                "success": entry.success,
                "error_message": entry.error_message,
                "metadata": entry.metadata,
            }
            f.write(json.dumps(log_data) + "\n")
    
    def query(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        user_id: Optional[str] = None,
        organization: Optional[str] = None,
        event_type: Optional[str] = None,
        success: Optional[bool] = None,
        limit: int = 100,
    ) -> List[AuditLogEntry]:
        """Query audit logs."""
        results = []
        
        for entry in reversed(self.entries):
            if start_date and entry.timestamp < start_date:
                continue
            if end_date and entry.timestamp > end_date:
                continue
            if user_id and entry.user_id != user_id:
                continue
            if organization and entry.organization != organization:
                continue
            if event_type and entry.event_type != event_type:
                continue
            if success is not None and entry.success != success:
                continue
            
            results.append(entry)
            if len(results) >= limit:
                break
        
        return results


# Global audit logger
audit_logger = AuditLogger()


# =============================================================================
# Authentication Dependencies
# =============================================================================

# Security schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)
bearer_auth = HTTPBearer(auto_error=False)


@dataclass
class AuthenticatedUser:
    """Authenticated user context."""
    user_id: str
    organization: str
    role: Role
    permissions: Set[Permission]
    api_key: Optional[APIKey] = None


async def get_api_key(
    request: Request,
    api_key_header: Optional[str] = Security(api_key_header),
    api_key_query: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """Extract API key from request."""
    return api_key_header or api_key_query


async def get_current_user(
    request: Request,
    api_key: Optional[str] = Depends(get_api_key),
) -> AuthenticatedUser:
    """
    Authenticate request and return user context.
    
    Checks:
    1. API key in header or query
    2. Bearer token (future: JWT/OAuth)
    """
    # Check if authentication is required
    auth_required = os.getenv("REQUIRE_AUTH", "false").lower() == "true"
    
    # Get client IP
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not client_ip and request.client:
        client_ip = request.client.host
    
    # Validate API key
    if api_key:
        validated_key = api_key_manager.validate_key(api_key, client_ip)
        if validated_key:
            return AuthenticatedUser(
                user_id=validated_key.key_id,
                organization=validated_key.organization,
                role=validated_key.role,
                permissions=ROLE_PERMISSIONS.get(validated_key.role, set()),
                api_key=validated_key,
            )
    
    # If auth required but no valid key
    if auth_required:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    # Default to anonymous viewer (for development)
    return AuthenticatedUser(
        user_id="anonymous",
        organization="public",
        role=Role.VIEWER,
        permissions=ROLE_PERMISSIONS[Role.VIEWER],
    )


def require_permission(permission: Permission):
    """Dependency to require a specific permission."""
    async def check_permission(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if permission not in user.permissions:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {permission.value} required",
            )
        return user
    return check_permission


def require_role(role: Role):
    """Dependency to require a specific role or higher."""
    role_hierarchy = [Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN]
    
    async def check_role(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if role_hierarchy.index(user.role) < role_hierarchy.index(role):
            raise HTTPException(
                status_code=403,
                detail=f"Role {role.value} or higher required",
            )
        return user
    return check_role


# =============================================================================
# Security Middleware
# =============================================================================

class SecurityHeaders:
    """Add security headers to responses."""
    
    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    
    @classmethod
    async def add_headers(cls, request: Request, call_next):
        response = await call_next(request)
        for header, value in cls.HEADERS.items():
            response.headers[header] = value
        return response


# =============================================================================
# Utility Functions
# =============================================================================

def create_api_key(
    name: str,
    role: str,
    organization: str,
    expires_in_days: Optional[int] = 365,
) -> Dict[str, str]:
    """
    Create a new API key (admin function).
    
    Returns the key only once - it cannot be retrieved later.
    """
    key = api_key_manager.generate_key()
    api_key = api_key_manager.register_key(
        key=key,
        name=name,
        role=Role(role),
        organization=organization,
        expires_in_days=expires_in_days,
    )
    
    return {
        "key": key,  # Only returned once!
        "key_id": api_key.key_id,
        "name": name,
        "role": role,
        "organization": organization,
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
    }
