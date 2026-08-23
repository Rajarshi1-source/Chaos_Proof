"""Positive-path check for the load plane: sample the live TestRun for 60s and
run the SAME validity gate the experiment runner uses. No fault is injected —
this answers exactly one question: is the measurement plane currently capable
of detecting failure?

Usage:  python -m src.check_load [floor]
"""

import os
import sys

from . import queries
from .integrations.prometheus import PrometheusClient
from .measurement.sampler import DualSourceSampler
from .measurement.validity import LoadFacts


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    floor = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    testrun = os.environ.get("TESTRUN", "steady-120rps")
    prom = PrometheusClient(os.environ.get("PROM_URL", "http://localhost:19090"))
    sampler = DualSourceSampler(prom, queries.client_queries(testrun), queries.server_queries())

    def fmt(v, spec=".2f"):
        return "-" if v is None else format(v, spec)

    def on_tick(s):
        print(f"  rps={fmt(s.client.get('client_rps'), '.1f')}"
              f"  avail={fmt(s.client.get('client_availability'), '.4f')}"
              f"  p99={fmt(s.client.get('client_p99_ms'), '.0f')}ms"
              f"  server_avail={fmt(s.server.get('server_availability'), '.4f')}")

    print(f"sampling 60s against testrun={testrun}, floor={floor:.0f} rps")
    samples = sampler.stream(duration_s=60, on_tick=on_tick)

    facts = LoadFacts(
        achieved_rps=samples.mean_client("client_rps"),
        dropped_iterations=samples.windowed_increase("dropped_iterations"),
        coverage=samples.coverage(),
    )
    ok = facts.is_valid(floor)
    print()
    print(f"achieved   : {facts.achieved_rps:.1f} rps  (floor {floor:.0f})")
    print(f"dropped    : {facts.dropped_iterations:.0f}")
    print(f"coverage   : {facts.coverage:.0%}")
    print(f"GATE       : {'VALID — measurement plane can detect failure' if ok else f'INVALID — {facts.invalidity_reason}'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
