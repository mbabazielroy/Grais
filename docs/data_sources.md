# Data Sources Guide

- **synthetic** (default): generated from config region lists; safe offline fallback.
- **real (Ontario sample)**: uses `config/ontario_real_regions.json` and `config/ontario_real_depots.json`.
- **remote**: fetch from URLs defined in config (`remoteRegionsUrl`, `remoteDepotsUrl`) with shape similar to the Ontario real files:
  - regions: `[{name, population, lat, lon, risk_factor, current_supply{res}, consumption_rate{res}}]`
  - depots: `[{name, lat, lon, stock{res}}]`

If remote/real load fails, system falls back to synthetic and surfaces warnings in metadata and UI.
