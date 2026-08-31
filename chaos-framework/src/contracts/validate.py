"""Running a contract's generated experiments and mapping results back to
clauses (§19.2).

The mapping is the interesting half. An experiment produces a hypothesis
verdict; a contract wants to know whether a CLAUSE was honoured. They are not
the same question, and three cases have to be kept apart:

    HELD       -> HONOURED   the claim survived the fault it declared
    FALSIFIED  -> VIOLATED   the claim is false, and the report says why
    INVALID / ABORTED / SKIPPED / DENIED
               -> UNTESTED   NOT "violated". A run that could not measure
                             anything has not falsified a claim, and recording
                             it as a violation would be the mirror image of the
                             empty-series bug: scoring absence as a result.
"""

import pathlib

import yaml

from ..scoring import scorer
from . import report as R
from .generator import Generated, generate
from .model import Contract

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
GENERATED_DIR = REPO_ROOT / "experiments" / "generated"

# The consumer's own client-side error rate, as a percentage, is what a
# `max_error_rate_pct` clause is really about — the CONSUMER's users, not the
# dependency's. Reading it server-side would miss exactly the failures that
# never reached a server.
OWN_ERROR_METRIC = "client_availability"

UNMEASURED = {"invalid", "aborted", "skipped", "denied", "error"}


def write_generated(contract: Contract, generated: list[Generated],
                    out_dir: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Materialise the testable experiments as YAML.

    Written to `experiments/generated/` rather than alongside the hand-written
    six, so `git status` shows immediately whether the suite was edited by a
    person or derived from a contract. A generated file that someone hand-edits
    is silently overwritten on the next generate, and keeping them separate is
    what makes that safe rather than surprising.
    """
    out_dir = out_dir or GENERATED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for g in generated:
        if not g.testable or g.spec.get("observation_only"):
            continue
        path = out_dir / f"{g.name}.yaml"
        header = (
            "# GENERATED FROM A RESILIENCE CONTRACT - DO NOT EDIT BY HAND.\n"
            f"# source: {contract.source_path}\n"
            f"# clause: {g.provenance.clause}\n"
            f"# regenerate: python -m src.chaosctl contracts generate "
            f"--service {contract.service} --write\n"
            "#\n"
            "# Advisory by construction: a generated experiment has no flakiness\n"
            "# history, so it cannot gate a merge until it earns that over 20\n"
            "# clean runs. Generation is not characterisation.\n")
        path.write_text(header + yaml.safe_dump(g.to_yaml_dict(), sort_keys=False),
                        encoding="utf-8")
        written.append(path)
    return written


def _own_error_rate_pct(samples) -> float | None:
    """Worst client-side error rate over the window, as a percentage."""
    series = [s.client.get(OWN_ERROR_METRIC) for s in samples.samples]
    present = [v for v in series if v is not None]
    if not present:
        return None
    return (1.0 - min(present)) * 100.0


def _worst_p99(samples) -> float | None:
    series = [s.client.get("client_p99_ms") for s in samples.samples]
    present = [v for v in series if v is not None]
    return max(present) if present else None


def clause_result_from_run(contract: Contract, g: Generated, result: dict) -> R.ClauseResult:
    """Map one experiment run onto its clause."""
    verdict = result["verdict"]
    samples = result.get("samples")
    execution_id = result.get("execution_id")
    summary = R.summarise_clause(g)

    if verdict.verdict in UNMEASURED:
        return R.ClauseResult(
            g.provenance.clause, summary, R.UNTESTED,
            observed=f"{verdict.verdict}: {verdict.reason or 'not measurable'}",
            execution_id=execution_id, declared=g.provenance.declared,
            testable=True)

    clause = g.provenance.clause
    dependency = clause.split(".")[1] if clause.startswith("tolerates.") else None

    if clause.endswith("max_error_rate_pct"):
        observed_pct = _own_error_rate_pct(samples) if samples else None
        observed = ("own errors unknown" if observed_pct is None
                    else f"own errors {observed_pct:.1f}%")
        outcome = R.HONOURED if verdict.verdict == "held" else R.VIOLATED
        mechanism = []
        if outcome == R.VIOLATED and dependency:
            mechanism = R.explain_retry_amplification(
                contract, dependency, g.provenance.declared, observed_pct or 0.0)
            if not mechanism and g.provenance.absorbed_by:
                mechanism = [f"the clause relied on {g.provenance.absorbed_by}; "
                             "that mechanism did not absorb the declared rate."]
        return R.ClauseResult(clause, summary, outcome, observed, mechanism,
                              execution_id, g.provenance.declared)

    if clause.endswith("max_latency_ms"):
        p99 = _worst_p99(samples) if samples else None
        observed = "P99 unknown" if p99 is None else f"P99 peaked {p99:.0f}ms"
        outcome = R.HONOURED if verdict.verdict == "held" else R.VIOLATED
        mechanism = []
        if outcome == R.VIOLATED and dependency:
            # A LATENCY clause, so the latency explainer. `declared` here is
            # milliseconds; passing it to the error-rate explainer would report
            # a "1000% error rate" with total confidence.
            mechanism = R.explain_latency_amplification(
                contract, dependency, g.provenance.declared, p99)
            if not mechanism and g.provenance.absorbed_by:
                mechanism = [f"the clause relied on {g.provenance.absorbed_by}; "
                             "that mechanism did not absorb the declared latency."]
        return R.ClauseResult(clause, summary, outcome, observed, mechanism,
                              execution_id, g.provenance.declared)

    outcome = R.HONOURED if verdict.verdict == "held" else R.VIOLATED
    return R.ClauseResult(clause, summary, outcome,
                          observed=verdict.reason or verdict.verdict,
                          execution_id=execution_id,
                          declared=g.provenance.declared)


def observe_inflict_clauses(prom, contract: Contract,
                            generated: list[Generated]) -> dict[str, R.ClauseResult]:
    """Measure the `does_not_inflict` clauses against live Prometheus.

    These are consumer-side promises about LOAD, so nothing is injected to
    check them — they are observed. Retry amplification is what pushes offered
    load above the promise, so where a dependency is retried the observed rate
    is decomposed into base traffic plus retried calls and the mechanism is
    named alongside the breach.
    """
    from .. import queries

    out: dict[str, R.ClauseResult] = {}
    dependencies = [t.dependency for t in contract.tolerates]

    for g in generated:
        if not g.spec.get("observation_only") or not g.testable:
            continue
        clause_id = g.provenance.clause
        threshold = g.spec.get("threshold")
        metric = g.spec.get("metric")
        summary = R.summarise_clause(g)

        if metric != "dependency_request_rate" or threshold is None:
            # Measurable in principle, not by this function. Left UNTESTED
            # rather than guessed at.
            continue

        worst_dep, worst_rate, retried = None, None, 0.0
        for dep in dependencies:
            q = queries.contract_queries(dep, contract.service)
            rate = prom.query_instant(q["dependency_request_rate"])
            if rate is None:
                continue
            if worst_rate is None or rate > worst_rate:
                worst_dep, worst_rate = dep, rate
                retried = prom.query_instant(q["retried_calls_rate"]) or 0.0

        if worst_rate is None:
            out[clause_id] = R.ClauseResult(
                clause_id, summary, R.UNTESTED,
                observed="no dependency request-rate series", testable=True)
            continue

        honoured = worst_rate <= threshold
        observed = f"peak {worst_rate:.1f} rps on {worst_dep}"
        mechanism: list[str] = []
        if not honoured:
            over = worst_rate - threshold
            mechanism.append(
                f"offered {worst_rate:.1f} rps against a declared ceiling of "
                f"{threshold:.0f} rps on {worst_dep} - over by {over:.1f} rps "
                f"({over / threshold:.0%}).")
            tol = next((t for t in contract.tolerates if t.dependency == worst_dep), None)
            if tol is not None and tol.retry_amplification is not None:
                amp = tol.retry_amplification
                mechanism.append(
                    f"{retried:.1f} rps of that is RETRIED calls "
                    f"(resilience4j retry `{worst_dep}`, maxAttempts="
                    f"{amp.attempts}). Retries are load the consumer generates, "
                    f"not load its users asked for, so a retry policy is a "
                    f"load-inflicting behaviour that this clause governs.")
                mechanism.append(
                    f"worst case is {amp.attempts}x the arrival rate if failures "
                    f"correlate - {amp.attempts * (worst_rate - retried):.0f} rps "
                    f"against a {threshold:.0f} rps promise.")
            mechanism.append(
                "the clause is violated at STEADY STATE as well as under fault, "
                "so this is a design mismatch rather than a chaos finding: the "
                "declared ceiling was never true.")
        out[clause_id] = R.ClauseResult(
            clause_id, summary, R.HONOURED if honoured else R.VIOLATED,
            observed, mechanism, declared=threshold)
    return out


def backfill(cur, contract: Contract, generated: list[Generated]) -> list[R.ClauseResult]:
    """Record clause outcomes for generated experiments that have ALREADY run.

    Adopting contracts on a system with history is the normal case, not the
    exception: the runs exist, `sli_samples` kept the raw observations, and
    re-deriving a clause outcome from them is the same operation retro-scoring
    performs for scores. Without this, a contract adopted today would report
    every clause UNTESTED until each one happened to be run again.

    Only the LATEST execution per experiment is used. An older run measured an
    older deployment, and a clause outcome is a statement about the system as
    it is now.
    """
    from ..hypothesis.engine import evaluate, parse_invariants
    from ..measurement.validity import LoadFacts
    from ..quality.retro import samples_for

    contract_id = register(cur, contract)
    out: list[R.ClauseResult] = []

    for g in generated:
        if not g.testable or g.spec.get("observation_only"):
            continue
        cur.execute(
            """SELECT e.id, e.verdict, e.verdict_reason, l.achieved_rps,
                      l.dropped_iterations, l.raw_summary
                 FROM experiment_executions e
                 LEFT JOIN load_runs l ON l.execution_id = e.id
                WHERE e.experiment = %s AND e.verdict IS NOT NULL
                ORDER BY e.id DESC LIMIT 1""", (g.name,))
        row = cur.fetchone()
        if row is None:
            continue
        execution_id, verdict_name, reason, achieved, dropped, raw = row
        samples = samples_for(cur, execution_id)
        if not samples.samples:
            continue

        raw = raw or {}
        facts = LoadFacts(
            achieved_rps=float(achieved or 0.0),
            dropped_iterations=float(dropped or 0),
            coverage=float(raw.get("coverage") or 0.0))
        v = evaluate(parse_invariants(g.spec["hypothesis"]["invariants"]),
                     samples, facts, float(g.spec["min_rps_floor"]))

        result = clause_result_from_run(
            contract, g, {"verdict": v, "samples": samples,
                          "execution_id": execution_id})
        record(cur, contract_id, result)
        out.append(result)
    return out


def load_recorded(cur, contract: Contract) -> dict[str, R.ClauseResult]:
    """Previously recorded validations for this contract, most recent per clause."""
    cur.execute(
        """SELECT DISTINCT ON (v.clause)
                  v.clause, v.outcome, v.observed, v.execution_id
             FROM contract_validations v
             JOIN resilience_contracts c ON c.id = v.contract_id
            WHERE c.service = %s AND c.version = %s
            ORDER BY v.clause, v.execution_id DESC, v.id DESC""",
        (contract.service, contract.version))
    out = {}
    for clause, outcome, observed, execution_id in cur.fetchall():
        observed = observed or {}
        out[clause] = R.ClauseResult(
            clause, "", outcome, observed.get("summary", ""),
            list(observed.get("mechanism") or []), execution_id,
            observed.get("declared"))
    return out


def register(cur, contract: Contract) -> str:
    """Record the contract itself, addressed by its claims."""
    sha = contract.sha256()
    cur.execute(
        """INSERT INTO resilience_contracts
               (service, version, provides, tolerates, does_not_inflict,
                contract_sha256)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (service, version) DO UPDATE
               SET contract_sha256 = EXCLUDED.contract_sha256
           RETURNING id""",
        (contract.service, contract.version,
         _json(contract.raw.get("provides") or {}),
         _json(contract.raw.get("tolerates") or []),
         _json(contract.raw.get("does_not_inflict") or []), sha))
    return cur.fetchone()[0]


def record(cur, contract_id: str, result: R.ClauseResult) -> None:
    """Record one clause outcome.

    A re-derivation REPLACES the previous derivation of the same clause from
    the same execution rather than appending beside it. These rows are analysis
    OF an execution, not the execution's own evidence: the run is immutable and
    lives in experiment_executions, while this is "what the current code
    concludes from it". Keeping both would leave a corrected explanation
    competing with the wrong one it fixed, decided by row order.
    """
    if result.execution_id is None:
        return
    cur.execute("""DELETE FROM contract_validations
                    WHERE contract_id = %s AND execution_id = %s AND clause = %s""",
                (contract_id, result.execution_id, result.clause))
    cur.execute(
        """INSERT INTO contract_validations
               (contract_id, execution_id, clause, outcome, observed)
           VALUES (%s, %s, %s, %s, %s)""",
        (contract_id, result.execution_id, result.clause, result.outcome,
         _json({"summary": result.observed, "mechanism": result.mechanism,
                "declared": result.declared})))


def report_for(contract: Contract, outcomes: dict[str, R.ClauseResult] | None = None,
               epoch: str | None = None, git_sha: str | None = None) -> R.ContractReport:
    return R.build(contract, generate(contract), outcomes or {}, epoch, git_sha)


def current_epoch_sha() -> str:
    return scorer.epoch_sha256([])


def _json(obj) -> str:
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
