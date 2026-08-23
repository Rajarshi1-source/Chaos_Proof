"""The dual-source sampler: client (k6) and server (Prometheus) series sampled
on a FIXED-DEADLINE schedule. Rev 1 slept AFTER sequential queries, so the
period was 5s plus query latency and every timeline drifted — here the next
tick is scheduled before the work, and the ACTUAL timestamp is recorded.

A query returning None (empty series) is stored as None, never as 0.0: a
missing series must never read as a passing series."""

import time
from dataclasses import dataclass, field

from ..constants import SAMPLE_INTERVAL_S
from ..integrations.prometheus import PrometheusClient


@dataclass
class Sample:
    sampled_at: float                      # actual wall-clock time of this tick
    client: dict[str, float | None]
    server: dict[str, float | None]


@dataclass
class SampleSet:
    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)

    def client_series(self, metric: str) -> list[float]:
        return [s.client[metric] for s in self.samples if s.client.get(metric) is not None]

    def coverage(self, metric: str = "client_rps") -> float:
        """Fraction of ticks where the client source produced data."""
        if not self.samples:
            return 0.0
        present = sum(1 for s in self.samples if s.client.get(metric) is not None)
        return present / len(self.samples)

    def mean_client(self, metric: str) -> float:
        series = self.client_series(metric)
        return sum(series) / len(series) if series else 0.0

    def last_client(self, metric: str) -> float | None:
        series = self.client_series(metric)
        return series[-1] if series else None

    def windowed_increase(self, metric: str) -> float:
        """Counter increase WITHIN the sampled window (last - first). The gate's
        verdict speaks about this window, so a startup transient that predates it
        (e.g. k6 dropping iterations while VUs initialise) must not invalidate it.
        Counter resets mid-window read as the absolute last value."""
        series = self.client_series(metric)
        if not series:
            return 0.0
        if len(series) == 1:
            return 0.0
        inc = series[-1] - series[0]
        return inc if inc >= 0 else series[-1]


class DualSourceSampler:

    def __init__(self, prom: PrometheusClient,
                 client_queries: dict[str, str], server_queries: dict[str, str]):
        self.prom = prom
        self.client_queries = client_queries
        self.server_queries = server_queries

    def snapshot(self) -> tuple[dict, dict]:
        def run(queries: dict[str, str]) -> dict[str, float | None]:
            out: dict[str, float | None] = {}
            for name, q in queries.items():
                try:
                    out[name] = self.prom.query_instant(q)
                except Exception:
                    out[name] = None       # a failed query is a MISSING sample, not a zero
            return out
        return run(self.client_queries), run(self.server_queries)

    def stream(self, duration_s: float, interval_s: int = SAMPLE_INTERVAL_S,
               on_tick=None) -> SampleSet:
        samples = SampleSet()
        start = time.monotonic()
        next_tick = start
        while time.monotonic() - start < duration_s:
            next_tick += interval_s
            sampled_at = time.time()               # record the ACTUAL timestamp
            client, server = self.snapshot()
            sample = Sample(sampled_at=sampled_at, client=client, server=server)
            samples.add(sample)
            if on_tick:
                on_tick(sample)
            time.sleep(max(0.0, next_tick - time.monotonic()))
        return samples
