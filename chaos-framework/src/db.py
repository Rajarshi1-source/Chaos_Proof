"""Evidence store writes (Phase 2 subset). One transaction per state transition:
the execution row, its load_run, and its samples land together — a half-written
execution silently skews aggregates (mlops-quality reference, consistency rules)."""

import json
import os

import psycopg

DSN = os.environ.get(
    "CHAOSPROOF_DB",
    "host=localhost port=5433 dbname=chaosproof user=chaosproof password=chaosproof",
)


def persist_execution(experiment: str, verdict: str, reason: str | None,
                      fault_injected_at, finished_at,
                      load_facts, target_rps: float, script_sha256: str,
                      samples) -> int:
    """Writes execution + load_run + sli_samples in ONE transaction; returns id."""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO experiment_executions
                   (experiment, verdict, verdict_reason, fault_injected_at, finished_at)
               VALUES (%s, %s, %s, to_timestamp(%s), to_timestamp(%s))
               RETURNING id""",
            (experiment, verdict, reason, fault_injected_at, finished_at),
        )
        execution_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO load_runs
                   (execution_id, tool, tool_version, workload_model, target_rps,
                    achieved_rps, dropped_iterations, client_error_rate, client_p99_ms,
                    script_sha256, raw_summary)
               VALUES (%s, 'k6', %s, 'open', %s, %s, %s, %s, %s, %s, %s)""",
            (
                execution_id, "2.2.0", target_rps,
                load_facts.achieved_rps, int(load_facts.dropped_iterations),
                _client_error_rate(samples), _client_p99(samples),
                script_sha256,
                json.dumps({"coverage": load_facts.coverage,
                            "invalidity_reason": load_facts.invalidity_reason}),
            ),
        )

        rows = []
        for s in samples.samples:
            for source, values in (("k6", s.client), ("prometheus", s.server)):
                for metric, value in values.items():
                    rows.append((execution_id, s.sampled_at, source, metric, value))
        cur.executemany(
            """INSERT INTO sli_samples (execution_id, sampled_at, source, metric, value)
               VALUES (%s, to_timestamp(%s), %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            rows,
        )
    return execution_id


def _client_error_rate(samples) -> float | None:
    availability = samples.client_series("client_availability")
    if not availability:
        return None
    return round(1 - (sum(availability) / len(availability)), 4)


def _client_p99(samples) -> float | None:
    series = samples.client_series("client_p99_ms")
    return max(series) if series else None
