"""Evidence store writes. One transaction per state transition: the execution
row, its load_run, its samples, and its per-invariant results land together —
a half-written execution silently skews aggregates (consistency rules)."""

import json
import os

import psycopg

DSN = os.environ.get(
    "CHAOSPROOF_DB",
    "host=localhost port=5433 dbname=chaosproof user=chaosproof password=chaosproof",
)


def ensure_experiment_type(cur, name: str, litmus_type: str,
                           fault_s: int, recovery_s: int) -> int:
    cur.execute(
        """INSERT INTO experiment_types (name, litmus_type, fault_duration_seconds,
                                         recovery_window_seconds)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (name) DO UPDATE SET litmus_type = EXCLUDED.litmus_type
           RETURNING id""",
        (name, litmus_type, fault_s, recovery_s))
    return cur.fetchone()[0]


def ensure_hypothesis(cur, experiment_type_id: int, version: int, description: str,
                      invariants: list[dict], min_rps_floor: float,
                      abort_conditions: list[dict]):
    """Insert-if-absent: a (experiment_type, version) pair is immutable once
    recorded. Changing a hypothesis means bumping its version (which also resets
    gating status — mlops-quality skill)."""
    cur.execute(
        """INSERT INTO hypotheses (experiment_type_id, version, description,
                                   invariants, min_rps_floor, abort_conditions)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (experiment_type_id, version) DO NOTHING""",
        (experiment_type_id, version, description,
         json.dumps(invariants), min_rps_floor, json.dumps(abort_conditions)))
    cur.execute(
        "SELECT id FROM hypotheses WHERE experiment_type_id = %s AND version = %s",
        (experiment_type_id, version))
    return cur.fetchone()[0]


def persist_run(*, spec: dict, verdict: str | None, reason: str | None,
                fault_injected_at: float, finished_at: float,
                load_facts, samples, script_sha256: str, git_sha: str | None,
                invariant_outcomes: list) -> int:
    """Everything about one run, one transaction. verdict None means the run was
    valid but unevaluated — never invent HELD."""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        type_id = ensure_experiment_type(
            cur, spec["name"], spec["litmus_fault"],
            int(spec["fault_duration_s"]), int(spec["recovery_window_s"]))
        hyp = spec["hypothesis"]
        hypothesis_id = ensure_hypothesis(
            cur, type_id, int(hyp["version"]), hyp["description"],
            hyp["invariants"], float(spec["min_rps_floor"]),
            spec.get("abort_conditions") or hyp.get("abort_conditions") or [])

        cur.execute(
            """INSERT INTO experiment_executions
                   (experiment, experiment_type_id, hypothesis_id, verdict,
                    verdict_reason, git_sha, fault_injected_at, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s))
               RETURNING id""",
            (spec["name"], type_id, hypothesis_id, verdict, reason, git_sha,
             fault_injected_at, finished_at))
        execution_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO load_runs
                   (execution_id, tool, tool_version, workload_model, target_rps,
                    achieved_rps, dropped_iterations, client_error_rate, client_p99_ms,
                    script_sha256, raw_summary)
               VALUES (%s, 'k6', %s, 'open', %s, %s, %s, %s, %s, %s, %s)""",
            (execution_id, "2.2.0", 120.0,
             load_facts.achieved_rps, int(load_facts.dropped_iterations),
             _client_error_rate(samples), _client_p99(samples), script_sha256,
             json.dumps({"coverage": load_facts.coverage,
                         "invalidity_reason": load_facts.invalidity_reason})))

        for o in invariant_outcomes:
            cur.execute(
                """INSERT INTO hypothesis_results
                       (execution_id, hypothesis_id, invariant_name, outcome,
                        worst_value, threshold, breached_for_s, evidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (execution_id, hypothesis_id, o.name, o.outcome,
                 o.worst_value, o.threshold, o.breached_for_s, json.dumps(o.evidence)))

        rows = [(execution_id, s.sampled_at, src, m, v)
                for s in samples.samples
                for src, vals in (("k6", s.client), ("prometheus", s.server))
                for m, v in vals.items()]
        cur.executemany(
            """INSERT INTO sli_samples (execution_id, sampled_at, source, metric, value)
               VALUES (%s, to_timestamp(%s), %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            rows)
    return execution_id


def _client_error_rate(samples) -> float | None:
    availability = samples.client_series("client_availability")
    if not availability:
        return None
    return round(1 - (sum(availability) / len(availability)), 4)


def _client_p99(samples) -> float | None:
    series = samples.client_series("client_p99_ms")
    return max(series) if series else None
