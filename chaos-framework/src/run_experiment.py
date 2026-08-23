"""Phase 2 minimal runner: load check -> inject -> dual-source sampling ->
validity gate -> persist. The hypothesis engine (Phase 3) and pre-flight/safety
plane (Phase 5) slot in around this skeleton; what is already structural here:
the validity gate runs on EVERY path, and an unmeasurable run is INVALID —
never PASS, never zero.

Usage:  python -m src.run_experiment ../experiments/pod_kill_payment.yaml
Env:    PROM_URL (default http://localhost:19090), TESTRUN (default steady-120rps)
"""

import hashlib
import os
import pathlib
import sys
import time

import yaml

from . import queries
from .db import persist_execution
from .integrations.litmus import LitmusClient
from .integrations.prometheus import PrometheusClient
from .measurement.sampler import DualSourceSampler
from .measurement.validity import LoadFacts


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)   # tick stream visible when redirected
    spec_path = pathlib.Path(sys.argv[1])
    spec = yaml.safe_load(spec_path.read_text())["experiment"]
    testrun = os.environ.get("TESTRUN", "steady-120rps")
    prom = PrometheusClient(os.environ.get("PROM_URL", "http://localhost:19090"))
    litmus = LitmusClient()

    name = spec["name"]
    ns = spec["target"]["namespace"]
    app = spec["target"]["app"]
    fault_s = int(spec["fault_duration_s"])
    recovery_s = int(spec["recovery_window_s"])
    floor = float(spec["min_rps_floor"])
    script = pathlib.Path(spec_path.parent.parent, spec["load_profile"])
    script_sha = hashlib.sha256(script.read_bytes()).hexdigest()

    sampler = DualSourceSampler(prom, queries.client_queries(testrun), queries.server_queries())

    print(f"experiment : {name}")
    print(f"target     : {app} in {ns}   fault pod-delete {fault_s}s + recovery {recovery_s}s")
    print(f"validity   : floor {floor:.0f} rps   testrun {testrun}")

    # --- STAGE 3: inject, then STAGE 4: sample both sources at fixed cadence ----
    engine = litmus.apply_pod_delete(ns, app, fault_s)
    fault_injected_at = time.time()
    print(f"injected   : chaosengine {engine}")

    def on_tick(sample):
        rps = sample.client.get("client_rps")
        avail = sample.client.get("client_availability")
        srv = sample.server.get("server_availability")
        print(f"  t+{sample.sampled_at - fault_injected_at:5.1f}s  "
              f"client_rps={_fmt(rps)}  client_avail={_fmt(avail)}  server_avail={_fmt(srv)}")

    try:
        samples = sampler.stream(duration_s=fault_s + recovery_s, on_tick=on_tick)
    finally:
        litmus.delete_engine(ns, engine)      # cleanup on EVERY path

    finished_at = time.time()
    chaos = litmus.chaos_result(ns, engine, spec["litmus_fault"])

    # --- STAGE V: the validity gate decides whether ANY verdict is scoreable ---
    dropped = samples.windowed_increase("dropped_iterations")   # drops IN the window, not lifetime
    facts = LoadFacts(
        achieved_rps=samples.mean_client("client_rps"),
        dropped_iterations=dropped,
        coverage=samples.coverage(),
    )

    if not facts.is_valid(floor):
        verdict, reason = "invalid", facts.invalidity_reason
    else:
        # Measurement is valid. HELD/FALSIFIED requires the hypothesis engine —
        # Phase 3. Refusing to claim a verdict we did not evaluate is the same
        # honesty rule that produces INVALID.
        verdict, reason = None, "measurement valid — hypothesis evaluation lands in Phase 3"

    execution_id = persist_execution(
        experiment=name,
        verdict=verdict if verdict else "error",
        reason=reason,
        fault_injected_at=fault_injected_at,
        finished_at=finished_at,
        load_facts=facts,
        target_rps=120.0,
        script_sha256=script_sha,
        samples=samples,
    ) if verdict else _persist_valid(name, reason, fault_injected_at, finished_at,
                                     facts, script_sha, samples)

    print()
    print(f"litmus     : verdict={chaos.get('verdict')} phase={chaos.get('phase')}")
    print(f"load       : achieved {facts.achieved_rps:.1f} rps  "
          f"dropped {facts.dropped_iterations:.0f}  coverage {facts.coverage:.0%}")
    print(f"execution  : #{execution_id}  ({len(samples.samples)} samples persisted)")
    if verdict == "invalid":
        print(f"VERDICT    : INVALID — {reason}")
        return 2
    print(f"VERDICT    : (deferred) {reason}")
    return 0


def _persist_valid(name, reason, fault_at, finished_at, facts, sha, samples) -> int:
    # Valid measurement, no hypothesis engine yet: record the run WITHOUT a verdict
    # rather than inventing one. 'error' is not right either — store NULL verdict.
    from .db import DSN
    import psycopg, json
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO experiment_executions
                   (experiment, verdict, verdict_reason, fault_injected_at, finished_at)
               VALUES (%s, NULL, %s, to_timestamp(%s), to_timestamp(%s)) RETURNING id""",
            (name, reason, fault_at, finished_at))
        execution_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO load_runs (execution_id, tool, tool_version, workload_model,
                   target_rps, achieved_rps, dropped_iterations, script_sha256, raw_summary)
               VALUES (%s,'k6','2.2.0','open',120,%s,%s,%s,%s)""",
            (execution_id, facts.achieved_rps, int(facts.dropped_iterations), sha,
             json.dumps({"coverage": facts.coverage})))
        rows = [(execution_id, s.sampled_at, src, m, v)
                for s in samples.samples
                for src, vals in (("k6", s.client), ("prometheus", s.server))
                for m, v in vals.items()]
        cur.executemany(
            """INSERT INTO sli_samples (execution_id, sampled_at, source, metric, value)
               VALUES (%s, to_timestamp(%s), %s, %s, %s) ON CONFLICT DO NOTHING""", rows)
    return execution_id


def _fmt(v) -> str:
    return "  -  " if v is None else f"{v:5.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
