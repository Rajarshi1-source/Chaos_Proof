"""Evidence store writes. One transaction per state transition: the execution
row, its load_run, its samples, and its per-invariant results land together —
a half-written execution silently skews aggregates (consistency rules)."""

import json
import os

import psycopg

from .scoring import scorer

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


def ensure_epoch(cur, gating_experiments: list[str], change_reason: str) -> int:
    """A scoring epoch is the hash of everything that changes the MEANING of a
    score: weights, the GATING experiment set, the SLO version, the scorer
    version. Epochs are immutable — a change opens a new one, never an UPDATE,
    which is what keeps a trend line from silently spanning a scorer edit."""
    sha = scorer.epoch_sha256(gating_experiments)
    cur.execute(
        """INSERT INTO scoring_epochs (epoch_sha256, weights, experiment_set,
                                       slo_version, scorer_version, change_reason)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (epoch_sha256) DO NOTHING""",
        (sha, json.dumps(scorer.WEIGHTS), sorted(gating_experiments),
         scorer.SLO_VERSION, scorer.SCORER_VERSION, change_reason))
    cur.execute("SELECT id FROM scoring_epochs WHERE epoch_sha256 = %s", (sha,))
    return cur.fetchone()[0]


def persist_run(*, spec: dict, verdict: str | None, reason: str | None,
                fault_injected_at: float, finished_at: float,
                load_facts, samples, script_sha256: str, git_sha: str | None,
                invariant_outcomes: list, check_list: list | None = None,
                score=None, blast_radius: dict | None = None,
                chaos_engine: str | None = None, preflight: dict | None = None,
                cleanup_log: dict | None = None, abort: dict | None = None) -> int:
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

        # Only GATING experiments count in the epoch's experiment_set; a new
        # experiment is advisory until characterised (mlops-quality), so Phase 4's
        # set is deliberately empty and every experiment here is advisory.
        cur.execute("SELECT t.name FROM experiment_types t "
                    "JOIN experiment_flakiness f ON f.experiment_type_id = t.id "
                    "WHERE f.gating IS TRUE ORDER BY t.name")
        gating = [r[0] for r in cur.fetchall()]
        epoch_id = ensure_epoch(cur, gating, "epoch 1 - initial scorer (Phase 4)")

        cur.execute(
            """INSERT INTO experiment_executions
                   (experiment, experiment_type_id, hypothesis_id, verdict,
                    verdict_reason, git_sha, fault_injected_at, finished_at,
                    scoring_epoch_id, score, weights_denominator,
                    blast_radius, chaos_engine, abort_reason, state)
               VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s),
                       %s, %s, %s, %s, %s, %s, 'finished')
               RETURNING id""",
            (spec["name"], type_id, hypothesis_id, verdict, reason, git_sha,
             fault_injected_at, finished_at, epoch_id,
             score.score if score else None,
             score.weights_denominator if score else None,
             json.dumps(blast_radius) if blast_radius else None,
             chaos_engine,
             (abort or {}).get("condition")))
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

        for c in (check_list or []):
            cur.execute(
                """INSERT INTO validation_checks
                       (execution_id, check_type, check_name, applicable, outcome,
                        score, expected_value, actual_value, message, details)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (execution_id, c.check_type, c.check_name, c.applicable, c.outcome,
                 c.score, c.expected_value, c.actual_value, c.message,
                 json.dumps(c.details)))

        if preflight:
            cur.execute(
                """INSERT INTO preflight_decisions
                       (execution_id, experiment, decision, reason, blast_radius, evidence)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (execution_id, spec["name"], preflight.get("decision", "proceed"),
                 preflight.get("reason"),
                 json.dumps(blast_radius) if blast_radius else None,
                 json.dumps(preflight.get("evidence", {}))))

        if abort:
            cur.execute(
                """INSERT INTO abort_events
                       (execution_id, path, condition, observed, threshold,
                        comparator, evidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (execution_id, abort["path"], abort.get("condition") or "unknown",
                 abort.get("observed"), abort.get("threshold"),
                 abort.get("comparator"), json.dumps(abort.get("evidence", {}))))

        if cleanup_log:
            cur.execute(
                """INSERT INTO cleanup_logs
                       (execution_id, steps, escalated, escalation_reason)
                   VALUES (%s, %s, %s, %s)""",
                (execution_id, json.dumps(cleanup_log.get("steps", [])),
                 cleanup_log.get("escalated", False),
                 cleanup_log.get("escalation_reason")))

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


def persist_refusal(spec: dict, verdict: str, reason: str, evidence: dict) -> int:
    """A refusal is EVIDENCE, not an absence. SKIPPED and DENIED get their own
    execution row so the dashboard can show that the framework declined and why —
    a silent no-op teaches nothing, a recorded refusal proves the guardrail fired."""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        type_id = ensure_experiment_type(
            cur, spec["name"], spec["litmus_fault"],
            int(spec["fault_duration_s"]), int(spec["recovery_window_s"]))
        cur.execute(
            """INSERT INTO experiment_executions
                   (experiment, experiment_type_id, verdict, verdict_reason,
                    finished_at, state)
               VALUES (%s, %s, %s, %s, now(), 'finished') RETURNING id""",
            (spec["name"], type_id, verdict, reason))
        execution_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO preflight_decisions
                   (execution_id, experiment, decision, reason, policy_rule, evidence)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (execution_id, spec["name"], verdict, reason,
             evidence.get("policy_rule"), json.dumps(evidence)))
    return execution_id
