from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

try:
    from prophet import Prophet  # type: ignore
except Exception:  # pragma: no cover
    Prophet = None  # type: ignore


class TimeSeriesForecaster:
    """
    Lightweight forecaster for supply gaps.
    Uses Prophet when available, otherwise falls back to a linear trend extrapolation.
    """

    def __init__(self, use_prophet: bool = True):
        self.use_prophet = use_prophet and Prophet is not None

    def forecast_gap(self, history: List[Dict[str, float]], horizon_days: int) -> List[float]:
        """
        history: list of {"day": int, "supply": float, "consumption": float}
        returns projected supply-consumption gap per day for the horizon.
        """
        if not history:
            return [0.0] * horizon_days

        gap = np.array([h["supply"] - h["consumption"] for h in history], dtype=float)
        days = np.array([h["day"] for h in history], dtype=float)

        if self.use_prophet:
            return self._forecast_prophet(days, gap, horizon_days)
        return self._forecast_trend(days, gap, horizon_days)

    def _forecast_trend(self, days: np.ndarray, gap: np.ndarray, horizon_days: int) -> List[float]:
        # Simple linear regression on (day, gap)
        coef = np.polyfit(days, gap, deg=1)
        slope, intercept = coef[0], coef[1]
        start = int(days.max()) + 1
        forecast = [slope * d + intercept for d in range(start, start + horizon_days)]
        return forecast

    def _forecast_prophet(self, days: np.ndarray, gap: np.ndarray, horizon_days: int) -> List[float]:
        import pandas as pd

        df = pd.DataFrame({"ds": pd.to_datetime(days, unit="D"), "y": gap})
        model = Prophet(daily_seasonality=False, weekly_seasonality=False, yearly_seasonality=False)
        model.fit(df)
        future = model.make_future_dataframe(periods=horizon_days, freq="D", include_history=False)
        forecast = model.predict(future)
        return forecast["yhat"].tolist()

    def backtest(self, history: List[Dict[str, float]], horizon_days: int, folds: int = 3) -> Dict[str, float]:
        """
        Rolling-origin backtest to get MAE on gap forecasts.
        """
        if len(history) < folds + horizon_days:
            return {"mae": None}
        mae_list: List[float] = []
        for i in range(folds):
            split = len(history) - (folds - i) * horizon_days
            train = history[:split]
            test = history[split : split + horizon_days]
            forecast = self.forecast_gap(train, horizon_days)
            actual = [h["supply"] - h["consumption"] for h in test]
            mae = np.mean(np.abs(np.array(forecast) - np.array(actual)))
            mae_list.append(float(mae))
        return {"mae": float(np.mean(mae_list)) if mae_list else None}
