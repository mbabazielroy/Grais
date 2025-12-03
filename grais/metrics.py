import threading
import time
from typing import Dict, List


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Dict[str, int] = {}
        self._latency_ms: Dict[str, List[float]] = {}
        self._solver_status: Dict[str, int] = {}
        self._unmet_total: float = 0.0

    def record_request(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self._requests[name] = self._requests.get(name, 0) + 1
            self._latency_ms.setdefault(name, []).append(duration_ms)

    def record_solver_status(self, status: str) -> None:
        with self._lock:
            self._solver_status[status] = self._solver_status.get(status, 0) + 1

    def record_unmet(self, total_unmet: float) -> None:
        with self._lock:
            self._unmet_total += total_unmet

    def snapshot(self) -> Dict:
        with self._lock:
            latency_stats = {
                name: {
                    "count": len(vals),
                    "p50": percentile(vals, 50),
                    "p90": percentile(vals, 90),
                    "p99": percentile(vals, 99),
                }
                for name, vals in self._latency_ms.items()
            }
            return {
                "requests": dict(self._requests),
                "latencyMs": latency_stats,
                "solverStatus": dict(self._solver_status),
                "unmetTotal": round(self._unmet_total, 2),
            }


def percentile(values: List[float], p: int) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    k = (len(vals) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return vals[int(k)]
    return vals[f] * (c - k) + vals[c] * (k - f)


metrics = Metrics()
