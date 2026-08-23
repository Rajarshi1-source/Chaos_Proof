"""Thin Prometheus HTTP API client. An empty result is returned as None —
NEVER coerced to 0.0, because a missing series must never read as a passing
series (hypothesis-engine skill, the empty-series rule)."""

import requests


class PrometheusClient:

    def __init__(self, base_url: str, timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def query_instant(self, promql: str) -> float | None:
        """Instant query. None when the result set is empty — the caller decides
        what absence means; this layer never invents a value."""
        r = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": promql},
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus error: {body}")
        result = body["data"]["result"]
        if not result:
            return None
        return float(result[0]["value"][1])

    def series_names(self, match: str) -> list[str]:
        """Discovery helper: label values of __name__ for a selector."""
        r = requests.get(
            f"{self.base_url}/api/v1/label/__name__/values",
            params={"match[]": match},
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        return r.json().get("data", [])
