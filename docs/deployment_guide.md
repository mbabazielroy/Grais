# GRAIS Deployment Guide for Government Organizations

This guide covers deploying GRAIS in production environments for government and enterprise customers.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Security Configuration](#security-configuration)
3. [Deployment Options](#deployment-options)
4. [High Availability Setup](#high-availability-setup)
5. [Compliance Considerations](#compliance-considerations)
6. [Monitoring & Operations](#monitoring--operations)

---

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Storage | 20 GB | 100+ GB SSD |
| Python | 3.10+ | 3.11+ |

### Network Requirements

- Outbound HTTPS (443) for external data sources
- Inbound on configured port (default 8000)
- No UDP required

---

## Security Configuration

### 1. Enable Authentication

Set the following environment variables to require API key authentication:

```bash
# Required for production
export REQUIRE_AUTH=true
export GRAIS_MASTER_API_KEY="your-secure-master-key-here"
```

Generate a secure master key:

```bash
python -c "import secrets; print(f'grais_{secrets.token_urlsafe(32)}')"
```

### 2. Create API Keys for Users

```python
from grais.security import create_api_key

# Create key for an organization
key_info = create_api_key(
    name="Ministry of Emergency Management",
    role="operator",
    organization="MEM",
    expires_in_days=365,
)
print(f"API Key: {key_info['key']}")  # Provide this to the organization ONCE
```

### 3. Role Hierarchy

| Role | Description | Typical Use |
|------|-------------|-------------|
| `viewer` | Read-only access to predictions and data | Public dashboards |
| `analyst` | Read + run scenarios | Planning staff |
| `operator` | Full operational access | Emergency coordinators |
| `admin` | System administration | IT administrators |

### 4. Rate Limiting

```bash
export RATE_LIMIT_ENABLED=true
export RATE_LIMIT_RPM=120  # Requests per minute
```

### 5. CORS Configuration

For production, specify allowed origins:

```bash
export CORS_ORIGINS="https://dashboard.gov.example.com,https://admin.gov.example.com"
```

---

## Deployment Options

### Option A: Docker (Recommended)

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run as non-root user
RUN useradd -m grais
USER grais

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t grais:latest .
docker run -d \
    -p 8000:8000 \
    -e REQUIRE_AUTH=true \
    -e GRAIS_MASTER_API_KEY="your-key" \
    -e ENVIRONMENT=production \
    grais:latest
```

### Option B: Kubernetes

`deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grais
  labels:
    app: grais
spec:
  replicas: 3
  selector:
    matchLabels:
      app: grais
  template:
    metadata:
      labels:
        app: grais
    spec:
      containers:
      - name: grais
        image: grais:latest
        ports:
        - containerPort: 8000
        env:
        - name: REQUIRE_AUTH
          value: "true"
        - name: ENVIRONMENT
          value: "production"
        - name: GRAIS_MASTER_API_KEY
          valueFrom:
            secretKeyRef:
              name: grais-secrets
              key: master-api-key
        resources:
          requests:
            memory: "2Gi"
            cpu: "500m"
          limits:
            memory: "4Gi"
            cpu: "2"
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
```

`service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: grais
spec:
  selector:
    app: grais
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
```

### Option C: Cloud Platforms

#### AWS ECS/Fargate

```bash
# Create task definition
aws ecs register-task-definition \
    --family grais \
    --container-definitions '[{
        "name": "grais",
        "image": "your-ecr-repo/grais:latest",
        "portMappings": [{"containerPort": 8000}],
        "environment": [
            {"name": "REQUIRE_AUTH", "value": "true"},
            {"name": "ENVIRONMENT", "value": "production"}
        ],
        "secrets": [
            {"name": "GRAIS_MASTER_API_KEY", "valueFrom": "arn:aws:secretsmanager:..."}
        ],
        "healthCheck": {
            "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
            "interval": 30,
            "timeout": 5
        }
    }]'
```

#### Azure Container Instances

```bash
az container create \
    --resource-group grais-rg \
    --name grais \
    --image your-acr.azurecr.io/grais:latest \
    --ports 8000 \
    --environment-variables \
        REQUIRE_AUTH=true \
        ENVIRONMENT=production \
    --secure-environment-variables \
        GRAIS_MASTER_API_KEY=your-key
```

---

## High Availability Setup

### Load Balancing

- Deploy 3+ replicas across availability zones
- Use health checks for automatic failover
- Configure sticky sessions if needed (not required for GRAIS)

### Database (Optional)

For persistent storage of tenant data, API keys, and audit logs, consider:

- PostgreSQL for relational data
- Redis for caching and rate limiting
- S3/Azure Blob for report storage

### Backup Strategy

1. **Configuration**: Version control (Git)
2. **API Keys**: Export regularly, store encrypted
3. **Audit Logs**: Ship to central logging system
4. **Reports**: Archive to object storage

---

## Compliance Considerations

### Data Residency

GRAIS can be deployed in specific regions to meet data sovereignty requirements:

- AWS: Choose specific regions (ca-central-1 for Canada)
- Azure: Use Canada Central, Canada East
- On-premises: Deploy in government data centers

### Audit Requirements

Enable comprehensive audit logging:

```bash
export LOG_LEVEL=INFO
export LOG_FORMAT=json
```

Audit logs capture:
- All API requests with user identity
- Response status and timing
- Client IP addresses
- Request payloads (sanitized)

### Security Hardening Checklist

- [ ] Enable HTTPS (TLS 1.3)
- [ ] Set `REQUIRE_AUTH=true`
- [ ] Configure rate limiting
- [ ] Restrict CORS origins
- [ ] Use secrets management for API keys
- [ ] Enable audit logging
- [ ] Configure network policies
- [ ] Regular security scanning
- [ ] Implement backup procedures

---

## Monitoring & Operations

### Metrics Endpoint

Access operational metrics at `/metrics`:

```json
{
    "requests": {
        "total": 15432,
        "by_method": {"GET": 8000, "POST": 7432}
    },
    "latency_ms": {
        "p50": 45,
        "p95": 120,
        "p99": 350
    },
    "solver_status": {
        "OPTIMAL": 7200,
        "FEASIBLE": 232
    }
}
```

### Prometheus Integration

Expose metrics in Prometheus format with a simple adapter:

```python
# metrics_adapter.py
from prometheus_client import Counter, Histogram, generate_latest

REQUEST_COUNT = Counter('grais_requests_total', 'Total requests')
REQUEST_LATENCY = Histogram('grais_request_latency_seconds', 'Request latency')
```

### Alerting Recommendations

| Alert | Condition | Severity |
|-------|-----------|----------|
| High Error Rate | 5xx > 5% for 5min | Critical |
| Slow Response | p95 > 2s for 10min | Warning |
| Solver Failures | INFEASIBLE > 10% | Warning |
| High Unmet Demand | Unmet > 20% | Critical |
| Rate Limit Hits | > 100/min | Info |

### Log Management

Recommended log pipeline:

1. Application logs → Fluentd/Filebeat
2. Aggregate in Elasticsearch/Splunk
3. Visualize in Kibana/Grafana
4. Archive to cold storage

---

## Support

For enterprise support inquiries:
- Technical documentation: `/docs` endpoint
- API documentation: `/redoc` or `/docs`
- Health status: `/health`

---

*GRAIS Enterprise Edition v0.3.0*
