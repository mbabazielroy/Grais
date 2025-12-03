from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np

try:
    from sklearn.linear_model import LinearRegression, LogisticRegression
    from sklearn.calibration import CalibratedClassifierCV
except Exception:  # pragma: no cover
    LogisticRegression = None
    LinearRegression = None
    CalibratedClassifierCV = None

from grais.data_generator import RegionData, generate_supply_history
from grais.forecasting import TimeSeriesForecaster


class ShortagePredictor:
    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.has_sklearn = LogisticRegression is not None and LinearRegression is not None
        self.prob_model = None
        self.day_model = LinearRegression() if self.has_sklearn else None
        self.day_std = 1.0
        self.trained = False
        if self.has_sklearn:
            self._train_baseline()
        else:
            # Heuristic mode; considered "trained" for flow control
            self.trained = True
        self.use_timeseries = os.getenv("USE_TIMESERIES_FORECAST", "0") == "1"
        self.ts_forecaster = TimeSeriesForecaster()

    def _train_baseline(self) -> None:
        rng = np.random.default_rng(self.random_state)
        samples = 600
        populations = rng.integers(200_000, 6_000_000, size=samples)
        supply = rng.uniform(0.5, 5.0, size=samples)  # supply per capita units
        consumption = rng.uniform(0.5, 3.5, size=samples)
        risk = rng.uniform(0.05, 1.0, size=samples)

        supply_gap = consumption - supply
        shortage_score = supply_gap * 1.8 + risk * 1.2 + rng.normal(0, 0.2, size=samples)
        prob = 1 / (1 + np.exp(-shortage_score))
        label = (prob > 0.5).astype(int)
        expected_day = np.clip(10 - shortage_score * 5 + rng.normal(0, 1, size=samples), 0, 30)
        # Add tail-heavy samples for better calibration in extremes
        extra_pop = rng.integers(10_000, 1_000_000, size=100)
        extra_supply = rng.uniform(0.1, 1.0, size=100)
        extra_consumption = rng.uniform(3.0, 6.0, size=100)
        extra_risk = rng.uniform(0.6, 1.0, size=100)
        supply_gap_extra = extra_consumption - extra_supply
        shortage_score_extra = supply_gap_extra * 2.0 + extra_risk * 1.5 + rng.normal(0, 0.3, size=100)
        prob_extra = 1 / (1 + np.exp(-shortage_score_extra))
        label_extra = (prob_extra > 0.5).astype(int)
        expected_day_extra = np.clip(5 - shortage_score_extra * 5 + rng.normal(0, 1, size=100), 0, 30)

        populations = np.concatenate([populations, extra_pop])
        supply = np.concatenate([supply, extra_supply])
        consumption = np.concatenate([consumption, extra_consumption])
        risk = np.concatenate([risk, extra_risk])
        label = np.concatenate([label, label_extra])
        expected_day = np.concatenate([expected_day, expected_day_extra])

        features = np.column_stack(
            [
                populations / 1e6,
                supply,
                consumption,
                risk,
            ]
        )
        base_lr = LogisticRegression(max_iter=1000)
        if CalibratedClassifierCV is not None:
            self.prob_model = CalibratedClassifierCV(base_lr, method="sigmoid", cv=3)
        else:
            self.prob_model = base_lr
        self.prob_model.fit(features, label)
        self.day_model.fit(features, expected_day)
        # simple std dev for day uncertainty
        preds_day = self.day_model.predict(features)
        self.day_std = float(np.std(preds_day - expected_day))
        self.trained = True

    def predict_shortages(
        self, regions: List[RegionData], horizon_days: int, mode: str, config: Dict
    ) -> List[Dict]:
        if not self.trained:
            raise RuntimeError("Predictor not trained")
        results = []
        for region in regions:
            features = self._build_features(region)
            if self.has_sklearn:
                prob_baseline = float(self.prob_model.predict_proba([features])[0][1])
                expected_day_baseline = max(0.0, float(self.day_model.predict([features])[0]))
            else:
                prob_baseline, expected_day_baseline = self._heuristic_prediction(region, features, horizon_days)

            if self.use_timeseries:
                ts_prob, ts_expected_day = self._timeseries_risk(region, horizon_days)
            else:
                ts_prob, ts_expected_day = None, None

            # Ensemble when both are available to improve stability
            if ts_prob is not None:
                prob = 0.6 * ts_prob + 0.4 * prob_baseline
            else:
                prob = prob_baseline

            if ts_expected_day is not None:
                expected_day = min(ts_expected_day, expected_day_baseline) if expected_day_baseline is not None else ts_expected_day
            else:
                expected_day = expected_day_baseline

            prob_lower, prob_upper = self._interval(prob)
            ed_lower, ed_upper = self._day_interval(expected_day)

            severity = self._severity(prob, expected_day, horizon_days)
            results.append(
                {
                    "region": region.name,
                    "population": region.population,
                    "shortageProbability": round(prob, 3),
                    "shortageProbLower": round(prob_lower, 3),
                    "shortageProbUpper": round(prob_upper, 3),
                    "shortageSeverity": severity,
                    "expectedShortageDay": round(expected_day, 1),
                    "expectedDayLower": round(ed_lower, 1),
                    "expectedDayUpper": round(ed_upper, 1),
                    "mode": mode,
                    "configAdminLevel": config.get("adminLevel"),
                    "coordinates": {
                        "lat": region.coordinates[0],
                        "lon": region.coordinates[1],
                    },
                    "inputs": {
                        "current_supply": region.current_supply,
                        "consumption_rate": region.consumption_rate,
                        "risk_factor": region.risk_factor,
                        "timeseries_enabled": self.use_timeseries,
                        "capabilities": config.get("capabilities"),
                    },
                }
            )
        return results

    @staticmethod
    def _severity(prob: float, expected_day: float, horizon: int) -> str:
        urgency = (horizon - expected_day) / max(horizon, 1)
        score = prob * 0.7 + max(0.0, urgency) * 0.3
        if score >= 0.7:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    @staticmethod
    def _build_features(region: RegionData) -> List[float]:
        # Aggregate across resources for coarse model
        total_supply = sum(region.current_supply.values())
        total_consumption = sum(region.consumption_rate.values())
        supply_per_cap = total_supply / max(region.population, 1)
        consumption_per_cap = total_consumption / max(region.population, 1)
        return [
            region.population / 1e6,
            supply_per_cap,
            consumption_per_cap,
            region.risk_factor,
        ]

    def _timeseries_risk(self, region: RegionData, horizon_days: int) -> (Optional[float], Optional[float]):
        history = generate_supply_history(region, days=max(30, horizon_days * 2))
        forecast_gap = self.ts_forecaster.forecast_gap(history, horizon_days)
        negatives = [g for g in forecast_gap if g < 0]
        prob = len(negatives) / max(len(forecast_gap), 1)
        expected_day = None
        for idx, g in enumerate(forecast_gap):
            if g < 0:
                expected_day = idx
                break
        return float(prob), float(expected_day) if expected_day is not None else None

    def _heuristic_prediction(
        self, region: RegionData, features: List[float], horizon_days: int
    ) -> (float, float):
        pop_m, supply_pc, consumption_pc, risk = features
        supply_gap = consumption_pc - supply_pc
        score = supply_gap * 2.0 + risk * 1.2
        prob = 1 / (1 + np.exp(-score))
        expected_day = max(0.0, horizon_days - score * 3)
        return float(prob), float(expected_day)

    def _interval(self, prob: float) -> (float, float):
        margin = max(0.05, 0.15 * (prob * (1 - prob)) ** 0.5)
        return max(0.0, prob - margin), min(1.0, prob + margin)

    def _day_interval(self, expected_day: float) -> (float, float):
        margin = max(1.0, self.day_std * 1.5 if self.day_std else 2.0)
        return max(0.0, expected_day - margin), expected_day + margin


@lru_cache(maxsize=2)
def get_predictor() -> ShortagePredictor:
    return ShortagePredictor()


def predict_shortages(regions: List[RegionData], horizon_days: int, mode: str, config: Dict) -> List[Dict]:
    predictor = get_predictor()
    return predictor.predict_shortages(regions, horizon_days, mode, config)
