"""
Smoke test for time-series forecaster integration.
Ensure USE_TIMESERIES_FORECAST=1 in env or set here.
Run: USE_TIMESERIES_FORECAST=1 python scripts/smoke_forecast.py
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import os

os.environ.setdefault("USE_TIMESERIES_FORECAST", "1")

from grais.config_loader import load_config
from grais.data_generator import generate_regions_from_config
from grais.prediction import predict_shortages
from grais.forecasting import TimeSeriesForecaster
from grais.data_generator import generate_supply_history


def run():
    cfg = load_config("ontario")
    regions = generate_regions_from_config(cfg)
    preds = predict_shortages(regions, cfg["timeHorizonDays"], cfg["mode"], cfg)
    print("Timeseries-enabled predictions sample:", preds[:2])

    forecaster = TimeSeriesForecaster()
    hist = generate_supply_history(regions[0], days=45)
    forecast = forecaster.forecast_gap(hist, horizon_days=7)
    backtest = forecaster.backtest(hist, horizon_days=7, folds=3)
    print("Forecast gap (first 5):", forecast[:5])
    print("Backtest MAE:", backtest)


if __name__ == "__main__":
    run()
