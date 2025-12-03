from __future__ import annotations

from typing import Dict, List


def local_prioritize(
    shortage_predictions: List[Dict],
    optimization_plan: Dict,
    mode: str,
    config: Dict,
) -> List[Dict]:
    """
    Lightweight, dependency-free "LLM" surrogate.
    Produces deterministic priorities and short explanations without external APIs.
    """
    unmet_map = {item["region"]: item["unmet"] for item in optimization_plan.get("unmet", [])}
    shipments = optimization_plan.get("shipments", [])
    supply_map: Dict[str, float] = {}
    for s in shipments:
        supply_map[s["to"]] = supply_map.get(s["to"], 0) + s["quantity"]

    results: List[Dict] = []
    for pred in shortage_predictions:
        region = pred["region"]
        prob = pred["shortageProbability"]
        severity = pred["shortageSeverity"]
        expected_day = pred["expectedShortageDay"]
        unmet = unmet_map.get(region, 0.0)
        supplied = supply_map.get(region, 0.0)

        # Deterministic score mixing probability, severity, unmet, and timeliness.
        severity_weight = {"high": 1.0, "medium": 0.65, "low": 0.35}.get(severity, 0.4)
        urgency = max(0.0, (config.get("timeHorizonDays", 7) - expected_day) / max(config.get("timeHorizonDays", 7), 1))
        score = prob * 0.55 + severity_weight * 0.25 + urgency * 0.15 - (0.1 if unmet > 0 else 0) + min(0.05, supplied / 20_000)
        score = max(0.0, min(score, 1.0))
        urgency_category = "critical" if score >= 0.7 else "high" if score >= 0.55 else "medium" if score >= 0.35 else "low"
        action = (
            "Push immediate shipments and activate surge logistics"
            if urgency_category in {"critical", "high"}
            else "Monitor and stage contingency stock"
        )
        explanation = (
            f"Mode={mode}, severity={severity}, prob={prob:.2f}, expectedDay={expected_day}, "
            f"unmet={unmet}, supplied={supplied:.1f}"
        )
        results.append(
            {
                "regionName": region,
                "priorityScore": round(score, 3),
                "urgencyCategory": urgency_category,
                "recommendedAction": action,
                "explanation": explanation,
            }
        )

    return sorted(results, key=lambda r: r["priorityScore"], reverse=True)
