# Architecture

## Overview
Textual diagram:
```
Config (global/ontario) -> Synthetic/real data loader
        -> Prediction (scikit-learn baseline)
        -> Optimization (OR-Tools LP)
        -> Prioritization (LLM + heuristics)
        -> API (FastAPI) -> Dashboard/API clients
```

## Data Flow
1. **Config loading** (`config_loader.py`): reads JSON in `config/` and fixes mode/admin level.  
2. **Data generation** (`data_generator.py`): creates region profiles (population, supply, consumption, risk), depots, and distance matrices (haversine). Real data can be swapped in here.  
3. **Prediction** (`prediction.py`): trains a baseline logistic/linear model on synthetic samples, then scores each region → shortageProbability, severity, expectedShortageDay.  
4. **Optimization** (`optimization.py`): linear program minimizing distance + unmet-demand penalty; constraints for depot stock, distance caps (`maxDistanceKm`). Returns shipments + unmet demand.  
5. **Prioritization** (`prioritization.py`): combines predictions + optimization; uses OpenAI if `OPENAI_API_KEY` is set, otherwise heuristic scoring.  
6. **API** (`main.py`): FastAPI endpoints `/predict`, `/optimize`, `/prioritize`, `/recommend` expose each stage or the whole pipeline.  
7. **Dashboard** (`frontend/index.html`): fetches `/recommend`, renders tables and a priority bar chart.

## Ontario Regional Fit
- `config/ontario.json` sets `mode: "regional"`, city admin level, tighter `maxDistanceKm`, and Ontario-centric base coordinates for synthetic data.
- Regional mode yields smaller distances, higher unmet penalties, and demand scaling per population to simulate intra-province logistics.

## Extensibility Notes
- **Real data**: Replace or augment `generate_regions_from_config` with a loader that reads CSV/DB; keep the `RegionData` schema stable.
- **Models**: Swap the baseline `ShortagePredictor` with time-series or probabilistic models; keep `predict_shortages` signature.
- **Optimization**: Extend `optimize_allocation` to multi-resource or time-window VRP by adding decision variables; OR-Tools already included.
- **LLM layer**: Switch providers by reading a `LLM_PROVIDER` env var and adding drivers in `prioritization.py`.
