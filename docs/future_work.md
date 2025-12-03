# Future Work

- **Real datasets**: Integrate Ontario open data (population, health unit demand, warehouse inventories) with a data loader that maps to `RegionData`. Add caching and validation layers.
- **Time-series forecasting**: Replace the baseline regression with sequence models (Prophet, Temporal Fusion Transformer) to capture trends and seasonality.
- **Multi-resource optimization**: Expand LP/VRP to handle per-resource vehicle capacity, cold chain constraints, and multi-day routing.
- **Risk fusion**: Ingest climate, mobility, and socio-economic indicators; learn a composite vulnerability index to weight shortages.
- **What-if simulation**: Expose scenarios (depot outage, budget cuts, surge events) and compare plans side-by-side.
- **Edge deployment**: Package a lightweight model bundle for disconnected environments with periodic sync to cloud services.
- **Observability**: Add tracing/metrics (OpenTelemetry) and decision audit logs for accountability.
