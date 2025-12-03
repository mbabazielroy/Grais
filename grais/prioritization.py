from __future__ import annotations

import json
import os
from typing import Dict, List

from grais.local_llm import local_prioritize


SEVERITY_WEIGHTS = {"high": 1.0, "medium": 0.65, "low": 0.35}


def prioritize_regions(
    shortage_predictions: List[Dict],
    optimization_plan: Dict,
    mode: str,
    config: Dict,
) -> List[Dict]:
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if provider == "local":
        return local_prioritize(shortage_predictions, optimization_plan, mode, config)

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            return _prioritize_with_llm(shortage_predictions, optimization_plan, mode, config, api_key)
        except Exception:
            # Fall back silently to heuristics if the LLM fails or is unavailable.
            pass
    return _heuristic_prioritization(shortage_predictions, optimization_plan, mode, config)


def _heuristic_prioritization(
    shortage_predictions: List[Dict], optimization_plan: Dict, mode: str, config: Dict
) -> List[Dict]:
    unmet_map = {item["region"]: item["unmet"] for item in optimization_plan.get("unmet", [])}
    allocations = optimization_plan.get("shipments", [])
    allocation_map: Dict[str, float] = {}
    for shipment in allocations:
        allocation_map[shipment["to"]] = allocation_map.get(shipment["to"], 0) + shipment["quantity"]

    results = []
    for pred in shortage_predictions:
        region = pred["region"]
        prob = pred["shortageProbability"]
        severity_weight = SEVERITY_WEIGHTS.get(pred["shortageSeverity"], 0.4)
        soonness = max(0.0, (config.get("timeHorizonDays", 7) - pred["expectedShortageDay"]) / max(config.get("timeHorizonDays", 7), 1))
        unmet_penalty = 0.2 if unmet_map.get(region, 0) > 0 else 0
        allocation_bonus = min(0.15, allocation_map.get(region, 0) / 10_000)

        score = prob * 0.6 + severity_weight * 0.25 + soonness * 0.1 + allocation_bonus - unmet_penalty
        urgency = "critical" if score >= 0.7 else "high" if score >= 0.55 else "medium" if score >= 0.35 else "low"
        action = (
            "Expedite shipments and pre-position reserves"
            if urgency in {"critical", "high"}
            else "Monitor and stage contingency stock"
        )
        results.append(
            {
                "regionName": region,
                "priorityScore": round(max(0.0, min(score, 1.0)), 3),
                "urgencyCategory": urgency,
                "recommendedAction": action,
                "explanation": f"Probability={prob:.2f}, severity={pred['shortageSeverity']}, expectedDay={pred['expectedShortageDay']}, unmet={unmet_map.get(region,0)}",
            }
        )

    return sorted(results, key=lambda r: r["priorityScore"], reverse=True)


def _prioritize_with_llm(
    shortage_predictions: List[Dict],
    optimization_plan: Dict,
    mode: str,
    config: Dict,
    api_key: str,
) -> List[Dict]:
    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL") or None,
    )
    system_prompt = (
        "You are an expert crisis planner. Given predicted shortages and allocation plans, "
        "rank regions and justify actions. Keep reasoning concise and operational."
    )
    user_prompt = {
        "mode": mode,
        "regionContext": config.get("region"),
        "shortagePredictions": shortage_predictions,
        "allocationPlan": optimization_plan,
    }
    completion = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Return a JSON array of {regionName, priorityScore (0-1), urgencyCategory (critical/high/medium/low), "
                    "recommendedAction, explanation}. Input:\n" + json.dumps(user_prompt)
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(completion.choices[0].message.content)
        results = parsed.get("results", parsed)
        if isinstance(results, list):
            return results
    except Exception:
        pass
    return _heuristic_prioritization(shortage_predictions, optimization_plan, mode, config)
