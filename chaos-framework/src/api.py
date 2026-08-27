"""The framework's HTTP surface (mlops-quality reference §7).

The dashboard's Route Handlers proxy THIS, rather than reading PostgreSQL
directly: one query layer, one set of types, and a read-only public build that
can point at a seeded API with no database credentials anywhere in the front end.

Two rules this module enforces at the source, so the UI cannot get them wrong
even by accident:

  - **A score is never returned without its epoch.** The epoch is what makes a
    score comparable to another score; a bare number invites exactly the
    comparison that scoring epochs exist to prevent.
  - **The trend is returned pre-segmented by epoch**, with boundaries as data.
    If the API handed back a flat list, every consumer would have to remember
    not to join across a boundary — and one day one of them would forget.

Run:  uvicorn src.api:app --port 8000
"""

import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row

from .db import DSN

# Verdicts that carry a comparable score. Everything else is excluded from the
# trend and from every aggregate — INVALID never contributes, not even as zero.
SCOREABLE = ("held", "falsified")


def _conn():
    return psycopg.connect(DSN, row_factory=dict_row)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast and loudly if the evidence store is unreachable: a dashboard
    # serving an empty page looks identical to a system with no experiments.
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:  # pragma: no cover - startup diagnostics
        print(f"WARNING: evidence store unreachable at startup: {e}")
    yield


app = FastAPI(title="ChaosProof", version="0.6.0", lifespan=lifespan)


@app.get("/api/health")
def health():
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM experiment_executions")
            n = cur.fetchone()["n"]
        return {"status": "ok", "executions": n}
    except Exception as e:
        raise HTTPException(503, f"evidence store unreachable: {e}")


@app.get("/api/epochs")
def epochs():
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT id, epoch_sha256, weights, experiment_set, slo_version,
                      scorer_version, started_at, ended_at, change_reason
                 FROM scoring_epochs ORDER BY id""")
        return [_epoch(r) for r in cur.fetchall()]


def _epoch(r: dict) -> dict:
    return {
        "id": r["id"], "sha256": r["epoch_sha256"], "weights": r["weights"],
        "experimentSet": list(r["experiment_set"] or []),
        "sloVersion": r["slo_version"], "scorerVersion": r["scorer_version"],
        "startedAt": r["started_at"].isoformat(),
        "endedAt": r["ended_at"].isoformat() if r["ended_at"] else None,
        "changeReason": r["change_reason"],
    }


@app.get("/api/score")
def current_score():
    """The latest scoreable execution — WITH its epoch. Never a bare number."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.experiment, e.score, e.weights_denominator,
                      e.finished_at, s.id AS epoch_id, s.epoch_sha256, s.change_reason
                 FROM experiment_executions e
                 LEFT JOIN scoring_epochs s ON s.id = e.scoring_epoch_id
                WHERE e.verdict = ANY(%s) AND e.score IS NOT NULL
                ORDER BY e.id DESC LIMIT 1""",
            (list(SCOREABLE),))
        row = cur.fetchone()
        if not row:
            return {"score": None, "epoch": None,
                    "reason": "no scoreable execution yet"}

        # The aggregate is over SCOREABLE runs in the CURRENT epoch only.
        cur.execute(
            """SELECT avg(score)::float AS avg, count(*) AS n
                 FROM experiment_executions
                WHERE verdict = ANY(%s) AND score IS NOT NULL
                  AND scoring_epoch_id = %s""",
            (list(SCOREABLE), row["epoch_id"]))
        agg = cur.fetchone()

    return {
        "score": float(row["score"]),
        "weightsDenominator": float(row["weights_denominator"] or 0) or None,
        "experiment": row["experiment"],
        "executionId": row["id"],
        "at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "epoch": {"id": row["epoch_id"], "sha256": row["epoch_sha256"],
                  "changeReason": row["change_reason"]},
        "epochAggregate": {"mean": agg["avg"], "n": agg["n"]},
    }


@app.get("/api/trends")
def trends(days: int = 30):
    """Pre-segmented by epoch. A flat list would leave every consumer one
    forgotten `if` away from drawing a line across a scorer change."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.score, e.verdict, e.finished_at, e.started_at,
                      e.scoring_epoch_id, s.epoch_sha256, s.change_reason,
                      s.started_at AS epoch_started
                 FROM experiment_executions e
                 LEFT JOIN scoring_epochs s ON s.id = e.scoring_epoch_id
                WHERE e.started_at >= now() - make_interval(days => %s)
                ORDER BY e.id""",
            (days,))
        rows = cur.fetchall()

    segments: list[dict] = []
    excluded: list[dict] = []
    for r in rows:
        at = (r["finished_at"] or r["started_at"]).isoformat()
        if r["verdict"] not in SCOREABLE or r["score"] is None:
            excluded.append({"executionId": r["id"], "at": at,
                             "verdict": r["verdict"]})
            continue
        eid = r["scoring_epoch_id"]
        if not segments or segments[-1]["epochId"] != eid:
            segments.append({"epochId": eid, "epochSha": r["epoch_sha256"],
                             "changeReason": r["change_reason"], "points": []})
        segments[-1]["points"].append({
            "executionId": r["id"], "at": at, "score": float(r["score"]),
            "epochId": eid,
            # Retro-scoring lands in Phase 8; until then nothing is re-derived.
            "retroScored": False,
        })

    boundaries = [
        {"epochId": s["epochId"], "at": s["points"][0]["at"],
         "changeReason": s["changeReason"]}
        for s in segments[1:] if s["points"]
    ]
    return {"segments": segments, "boundaries": boundaries, "excluded": excluded}


@app.get("/api/validity")
def validity(limit: int = 40):
    """Achieved rps against the declared floor, per run. The panel that makes the
    project's core discipline visible at a glance."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.verdict, e.started_at, e.finished_at,
                      l.achieved_rps, l.dropped_iterations,
                      l.raw_summary, h.min_rps_floor
                 FROM experiment_executions e
                 LEFT JOIN load_runs l ON l.execution_id = e.id
                 LEFT JOIN hypotheses h ON h.id = e.hypothesis_id
                ORDER BY e.id DESC LIMIT %s""",
            (limit,))
        rows = cur.fetchall()

    out = []
    for r in reversed(rows):
        summary = r["raw_summary"] or {}
        floor = float(r["min_rps_floor"]) if r["min_rps_floor"] is not None else None
        rps = float(r["achieved_rps"]) if r["achieved_rps"] is not None else None
        out.append({
            "executionId": r["id"],
            "at": (r["finished_at"] or r["started_at"]).isoformat(),
            "achievedRps": rps, "floor": floor, "verdict": r["verdict"],
            "valid": r["verdict"] != "invalid",
            "reason": summary.get("invalidity_reason"),
        })
    return out


@app.get("/api/executions")
def executions(limit: int = 50):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.experiment, e.verdict, e.verdict_reason, e.score,
                      e.weights_denominator, e.scoring_epoch_id, e.git_sha,
                      e.started_at, e.fault_injected_at, e.finished_at,
                      s.epoch_sha256
                 FROM experiment_executions e
                 LEFT JOIN scoring_epochs s ON s.id = e.scoring_epoch_id
                ORDER BY e.id DESC LIMIT %s""",
            (limit,))
        return [_execution_row(r) for r in cur.fetchall()]


def _execution_row(r: dict) -> dict:
    return {
        "id": r["id"], "experiment": r["experiment"],
        "verdictKind": r["verdict"], "verdictReason": r["verdict_reason"],
        "score": float(r["score"]) if r["score"] is not None else None,
        "weightsDenominator": (float(r["weights_denominator"])
                               if r["weights_denominator"] is not None else None),
        "epochId": r["scoring_epoch_id"], "epochSha": r.get("epoch_sha256"),
        "gitSha": r["git_sha"],
        "startedAt": r["started_at"].isoformat(),
        "faultInjectedAt": (r["fault_injected_at"].isoformat()
                            if r["fault_injected_at"] else None),
        "finishedAt": r["finished_at"].isoformat() if r["finished_at"] else None,
    }


@app.get("/api/executions/{execution_id}")
def execution(execution_id: int):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT e.*, s.epoch_sha256
                 FROM experiment_executions e
                 LEFT JOIN scoring_epochs s ON s.id = e.scoring_epoch_id
                WHERE e.id = %s""", (execution_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"no execution {execution_id}")

        cur.execute(
            """SELECT invariant_name, outcome, worst_value, threshold,
                      breached_for_s, evidence
                 FROM hypothesis_results WHERE execution_id = %s ORDER BY id""",
            (execution_id,))
        invariants = [{
            "name": r["invariant_name"], "outcome": r["outcome"],
            "worstValue": float(r["worst_value"]) if r["worst_value"] is not None else None,
            "threshold": float(r["threshold"]) if r["threshold"] is not None else None,
            "breachedForS": float(r["breached_for_s"] or 0),
            "kind": (r["evidence"] or {}).get("kind", "-"),
            "evidence": r["evidence"] or {},
        } for r in cur.fetchall()]

        cur.execute(
            """SELECT check_type, check_name, applicable, outcome, score,
                      expected_value, actual_value, message
                 FROM validation_checks WHERE execution_id = %s ORDER BY id""",
            (execution_id,))
        checks = [{
            "checkType": r["check_type"], "checkName": r["check_name"],
            "applicable": r["applicable"], "outcome": r["outcome"],
            "score": float(r["score"]) if r["score"] is not None else None,
            "expectedValue": r["expected_value"], "actualValue": r["actual_value"],
            "message": r["message"],
        } for r in cur.fetchall()]

        cur.execute(
            """SELECT path, condition, observed, threshold, comparator, tripped_at
                 FROM abort_events WHERE execution_id = %s ORDER BY id""",
            (execution_id,))
        aborts = [{
            "path": r["path"], "condition": r["condition"],
            "observed": float(r["observed"]) if r["observed"] is not None else None,
            "threshold": float(r["threshold"]) if r["threshold"] is not None else None,
            "comparator": r["comparator"],
            "trippedAt": r["tripped_at"].isoformat(),
        } for r in cur.fetchall()]

        cur.execute("SELECT steps, escalated, escalation_reason FROM cleanup_logs "
                    "WHERE execution_id = %s ORDER BY id DESC LIMIT 1", (execution_id,))
        cleanup = cur.fetchone()

        cur.execute(
            """SELECT tool, tool_version, workload_model, target_rps, achieved_rps,
                      dropped_iterations, client_error_rate, client_p99_ms, raw_summary
                 FROM load_runs WHERE execution_id = %s LIMIT 1""", (execution_id,))
        load = cur.fetchone()

        # The dual-source overlay: the gap between them estimates requests that
        # died before reaching any server.
        cur.execute(
            """SELECT sampled_at, source, metric, value FROM sli_samples
                WHERE execution_id = %s AND metric IN
                      ('client_availability','server_availability','client_rps')
                ORDER BY sampled_at""", (execution_id,))
        samples = [{
            "at": r["sampled_at"].isoformat(), "source": r["source"],
            "metric": r["metric"],
            "value": float(r["value"]) if r["value"] is not None else None,
        } for r in cur.fetchall()]

    out = _execution_row(dict(row) | {"epoch_sha256": row.get("epoch_sha256")})
    out.update({
        "invariants": invariants, "checks": checks, "aborts": aborts,
        "blastRadius": row.get("blast_radius"),
        "cleanup": ({"steps": cleanup["steps"], "escalated": cleanup["escalated"],
                     "escalationReason": cleanup["escalation_reason"]}
                    if cleanup else None),
        "load": ({"tool": load["tool"], "toolVersion": load["tool_version"],
                  "workloadModel": load["workload_model"],
                  "targetRps": float(load["target_rps"]),
                  "achievedRps": (float(load["achieved_rps"])
                                  if load["achieved_rps"] is not None else None),
                  "droppedIterations": load["dropped_iterations"],
                  "clientErrorRate": (float(load["client_error_rate"])
                                      if load["client_error_rate"] is not None else None),
                  "clientP99Ms": (float(load["client_p99_ms"])
                                  if load["client_p99_ms"] is not None else None),
                  "coverage": (load["raw_summary"] or {}).get("coverage"),
                  "invalidityReason": (load["raw_summary"] or {}).get("invalidity_reason")}
                 if load else None),
        "samples": samples,
    })
    return out


@app.get("/api/preflight")
def preflight_decisions(limit: int = 30):
    """Refusals are evidence. SKIPPED and DENIED get first-class treatment
    because 'the framework correctly refused to run' proves the guardrails are
    load-bearing rather than decorative."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """SELECT execution_id, experiment, decision, reason, policy_rule,
                      blast_radius, decided_at
                 FROM preflight_decisions ORDER BY id DESC LIMIT %s""", (limit,))
        return [{
            "executionId": r["execution_id"], "experiment": r["experiment"],
            "decision": r["decision"], "reason": r["reason"],
            "policyRule": r["policy_rule"], "blastRadius": r["blast_radius"],
            "at": r["decided_at"].isoformat(),
        } for r in cur.fetchall()]


# NOTE: there is deliberately NO trigger endpoint here. No route that can inject
# a fault is reachable from the dashboard; the only path that can is inside the
# cluster. See the read-only build flag in the front end.
