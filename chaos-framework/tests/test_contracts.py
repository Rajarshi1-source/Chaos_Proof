"""Resilience contracts (§19) — the model, the generator, the report, the diff.

All hermetic. The contract system's value is that a claim about architecture
becomes a testable artefact, so the machinery that turns a clause into an
experiment has to be at least as trustworthy as a hand-written one.
"""

import pathlib

import pytest
import yaml

from src.contracts import diff as D
from src.contracts import generator as G
from src.contracts import model as M
from src.contracts import report as R

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = sorted((REPO_ROOT / "target-app").glob("*/resilience-contract.yaml"))


@pytest.fixture(scope="module")
def order_api() -> M.Contract:
    return M.for_service("order-api")


# --------------------------------------------------------------------------- #
# The shipped contracts must stay well-formed — they are consumed by CI.
# --------------------------------------------------------------------------- #

def test_every_service_ships_a_contract():
    services = {p.name for p in (REPO_ROOT / "target-app").iterdir()
                if p.is_dir() and (p / "pom.xml").exists()}
    declared = {M.load(p).service for p in CONTRACTS}
    assert services == declared, f"services without a contract: {services - declared}"


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.parent.name)
def test_contract_parses_and_addresses_its_claims(path):
    c = M.load(path)
    assert c.service and c.version
    assert len(c.sha256()) == 64
    assert c.clause_ids(), "a contract with no addressable clause tests nothing"


def test_missing_tolerates_key_is_an_error_but_empty_list_is_not():
    """An empty list and a missing key are DIFFERENT claims: the first says
    'leaf service, every failure originates here', the second is an oversight.
    The generator must not have to guess which it is looking at."""
    base = {"service": "x", "version": "1.0.0",
            "provides": {"availability_slo": 99.0, "latency_p99_ms": 100}}
    with pytest.raises(M.ContractError, match="tolerates"):
        M.parse(dict(base))
    assert M.parse({**base, "tolerates": []}).tolerates == []


def test_contract_hash_ignores_comments_and_key_order():
    """A reformatting commit must not read as a contract change, or the CI diff
    cries wolf and gets ignored."""
    a = {"service": "x", "version": "1.0.0",
         "provides": {"availability_slo": 99.0, "latency_p99_ms": 100},
         "tolerates": [], "does_not_inflict": []}
    b = {"does_not_inflict": [], "tolerates": [], "version": "9.9.9",
         "provides": {"latency_p99_ms": 100, "availability_slo": 99.0},
         "service": "x", "owners": ["@someone"]}
    assert M.parse(a).sha256() == M.parse(b).sha256()


def test_changing_a_promise_changes_the_hash():
    a = {"service": "x", "version": "1.0.0", "tolerates": [],
         "provides": {"availability_slo": 99.0, "latency_p99_ms": 100}}
    b = {**a, "provides": {"availability_slo": 99.0, "latency_p99_ms": 900}}
    assert M.parse(a).sha256() != M.parse(b).sha256()


# --------------------------------------------------------------------------- #
# Retry amplification — the arithmetic behind the project's best finding.
# --------------------------------------------------------------------------- #

def test_effective_error_rate_is_the_at_least_one_failure_probability():
    """3 attempts against 5% upstream: 1 - 0.95^3 = 14.26%. This is the number
    the plan quotes as '3 attempts x 5% = 14% effective', and it is the reason
    'I tolerate 5%' is not the same claim as '5% of my calls see an error'."""
    amp = M.RetryAmplification(attempts=3, wait_ms=500)
    assert amp.effective_error_rate(5.0) == pytest.approx(14.2625, abs=1e-4)
    assert amp.effective_error_rate(10.0) == pytest.approx(27.1, abs=1e-4)


def test_effective_error_rate_falls_with_fewer_attempts():
    """The remedy has to actually help, or naming it is noise."""
    at3 = M.RetryAmplification(3, 500).effective_error_rate(5.0)
    at2 = M.RetryAmplification(2, 500).effective_error_rate(5.0)
    assert at2 < at3


def test_request_amplification_is_not_the_error_arithmetic():
    """The two are routinely conflated. Expected downstream requests per logical
    call at 5% upstream failure is 1.0525 — nothing like 14%. Confusing them
    produces either false alarm or false comfort depending on direction."""
    amp = M.RetryAmplification(attempts=3, wait_ms=500)
    assert amp.request_amplification(5.0) == pytest.approx(1.0525, abs=1e-4)
    assert amp.request_amplification(100.0) == pytest.approx(3.0)


def test_worst_case_latency_is_attempts_times_timeout_plus_waits():
    amp = M.RetryAmplification(attempts=3, wait_ms=500)
    assert amp.worst_case_latency(3000) == 3 * 3000 + 2 * 500


def test_order_api_declares_its_retry_amplification(order_api):
    """Cross-cutting rule: never add retry without accounting for amplification
    in the contract. order-api retries inventory-service, so the clause must
    carry the arithmetic rather than leaving it to be discovered."""
    tol = next(t for t in order_api.tolerates if t.dependency == "inventory-service")
    assert tol.retry_amplification is not None
    assert tol.retry_amplification.attempts == 3


def test_declared_amplification_matches_the_running_config(order_api):
    """The contract's `attempts` must equal resilience4j's `maxAttempts`. A
    contract that describes a retry policy the service does not have is worse
    than no contract: it is a confident, wrong answer."""
    app_yaml = yaml.safe_load(
        (REPO_ROOT / "target-app" / "order-api" / "src" / "main" / "resources"
         / "application.yaml").read_text(encoding="utf-8"))
    configured = app_yaml["resilience4j"]["retry"]["instances"]["inventoryService"]
    tol = next(t for t in order_api.tolerates if t.dependency == "inventory-service")
    assert tol.retry_amplification.attempts == configured["maxAttempts"]
    assert f"{tol.retry_amplification.wait_ms}ms" == configured["waitDuration"]


def test_declared_effective_rate_matches_the_computed_one(order_api):
    """The contract publishes `effective_error_rate_pct_at_max` as a convenience
    for readers. If it drifts from the formula the document is lying in the most
    persuasive way possible — with a specific number."""
    tol = next(t for t in order_api.tolerates if t.dependency == "inventory-service")
    computed = tol.retry_amplification.effective_error_rate(tol.max_error_rate_pct)
    assert tol.retry_amplification.effective_error_rate_pct_at_max == pytest.approx(
        computed, abs=0.05)


def test_the_mechanism_is_named_only_where_a_retry_exists(order_api):
    """'Names the mechanism WHERE IT CAN.' payment-service has a circuit
    breaker, not a retry, so no amplification story may be invented for it."""
    with_retry = R.explain_retry_amplification(order_api, "inventory-service", 10.0, 12.1)
    without = R.explain_retry_amplification(order_api, "payment-service", 5.0, 12.1)
    assert with_retry and any("retry budget amplifies" in l for l in with_retry)
    assert without == []


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def test_one_experiment_per_testable_clause(order_api):
    gen = G.generate(order_api)
    assert {g.provenance.clause for g in gen} == set(order_api.clause_ids()) - {
        "provides.availability_slo", "provides.latency_p99_ms"}


def test_every_generated_experiment_carries_provenance(order_api):
    for g in G.generate(order_api):
        assert g.provenance.clause
        assert g.provenance.service == order_api.service
        assert g.provenance.contract_version == order_api.version


def test_generated_experiments_carry_a_floor_and_abort_conditions(order_api):
    """A machine-written experiment is not a safer experiment. The suite
    mandate applies to generated YAML exactly as it does to hand-written."""
    for g in G.generate(order_api):
        if not g.testable or g.spec.get("observation_only"):
            continue
        assert g.spec["min_rps_floor"] > 0
        assert g.spec["hypothesis"]["abort_conditions"], g.name


def test_generated_experiments_are_advisory(order_api):
    """Generation is not characterisation. An experiment that has never run
    cannot have earned the right to block a merge."""
    for g in G.generate(order_api):
        if g.testable and not g.spec.get("observation_only"):
            assert g.spec["gating"] is False


def test_the_injected_value_comes_from_the_clause(order_api):
    """'I tolerate 800ms' must inject exactly 800ms. If the experiment and the
    clause can drift, the experiment stops validating the claim."""
    gen = {g.provenance.clause: g for g in G.generate(order_api)}
    latency = gen["tolerates.payment-service.max_latency_ms"]
    assert latency.spec["params"]["NETWORK_LATENCY"] == "800"
    errors = gen["tolerates.inventory-service.max_error_rate_pct"]
    assert errors.spec["params"]["NETWORK_PACKET_LOSS_PERCENTAGE"] == "10"


def test_the_invariant_asserts_the_consumers_own_slo(order_api):
    """The clause is about the DEPENDENCY's behaviour; the invariant must be
    about the CONSUMER's promise. That asymmetry is what lets amplification
    show up at all."""
    gen = {g.provenance.clause: g for g in G.generate(order_api)}
    inv = gen["tolerates.payment-service.max_error_rate_pct"].spec["hypothesis"]["invariants"]
    availability = next(i for i in inv if i["name"] == "consumer_availability_holds")
    assert availability["threshold"] == pytest.approx(order_api.provides.availability_slo / 100)


def test_an_uninjectable_dependency_is_untested_not_omitted(order_api):
    """redis has no deployment to target. The clause is still a promise, so it
    must appear as UNTESTED rather than vanish — an invisible gap is just a
    hole."""
    gen = {g.provenance.clause: g for g in G.generate(order_api)}
    redis = gen["tolerates.redis.max_outage_seconds"]
    assert not redis.testable
    assert "redis" in redis.untested_reason


def test_a_stateful_dependency_is_untested_by_policy():
    """inventory-service tolerates postgres, and policy denies stateful targets.
    The generator must not emit an experiment the safety plane would refuse."""
    gen = {g.provenance.clause: g for g in G.generate(M.for_service("inventory-service"))}
    pg = gen["tolerates.postgres.max_error_rate_pct"]
    assert not pg.testable
    assert "stateful" in pg.untested_reason


def test_an_outage_clause_longer_than_the_fault_window_says_so(order_api):
    """A 300s claim validated by a 60s outage is not validated, and the
    description has to admit that rather than implying full coverage."""
    gen = {g.provenance.clause: g for g in G.generate(order_api)}
    outage = gen["tolerates.inventory-service.max_outage_seconds"]
    assert outage.spec["fault_duration_s"] == G.FAULT_DURATION_S
    assert "not 300s" in outage.spec["hypothesis"]["description"]


def test_generated_yaml_round_trips(order_api):
    for g in G.generate(order_api):
        if g.testable and not g.spec.get("observation_only"):
            body = yaml.safe_load(yaml.safe_dump(g.to_yaml_dict()))["experiment"]
            assert body["provenance"]["clause"] == g.provenance.clause


# --------------------------------------------------------------------------- #
# Coverage — UNTESTED must be reported, never silently dropped.
# --------------------------------------------------------------------------- #

def test_coverage_counts_untestable_clauses_in_the_denominator(order_api):
    """Otherwise a service raises its coverage by making a clause
    unfalsifiable, which is exactly backwards."""
    gen = G.generate(order_api)
    rep = R.build(order_api, gen)
    assert rep.total == len(gen)
    assert rep.coverage_ratio == 0.0          # nothing run yet
    assert rep.unfalsifiable, "redis clause should be unfalsifiable"


def test_validated_and_falsifiable_are_different_ratios(order_api):
    """'Nobody ran it' and 'we cannot run it' are different findings, and the
    second is the one worth arguing about."""
    gen = G.generate(order_api)
    rep = R.build(order_api, gen)
    assert rep.testable_ratio > rep.coverage_ratio


def test_the_coverage_metric_is_exported(order_api):
    rep = R.build(order_api, G.generate(order_api))
    text = rep.prometheus_metrics()
    assert "chaosproof_contract_clause_coverage_ratio" in text
    assert f'service="{order_api.service}"' in text


def test_an_unmeasurable_run_is_untested_not_violated(order_api):
    """The mirror of the empty-series rule: a run that measured nothing has not
    falsified a claim, and recording it as a violation would score absence as a
    result."""
    from src.contracts import validate as V
    gen = {g.provenance.clause: g for g in G.generate(order_api)}
    g = gen["tolerates.payment-service.max_error_rate_pct"]

    class _V:
        verdict = "invalid"
        reason = "achieved 0 rps"
    result = V.clause_result_from_run(order_api, g, {"verdict": _V(), "samples": None,
                                                     "execution_id": 7})
    assert result.outcome == R.UNTESTED
    assert result.outcome != R.VIOLATED


# --------------------------------------------------------------------------- #
# The CI diff — surfaces a weakening, never blocks it.
# --------------------------------------------------------------------------- #

def _contract(**over) -> M.Contract:
    raw = {"service": "s", "version": "1.0.0", "owners": ["@o"],
           "provides": {"availability_slo": 99.5, "latency_p99_ms": 500,
                        "graceful_degradation_modes": ["m1"]},
           "tolerates": [{"dependency": "d", "max_error_rate_pct": 5}],
           "does_not_inflict": []}
    raw.update(over)
    return M.parse(raw)


def test_raising_a_tolerance_is_flagged_as_weakening():
    """Raising max_error_rate_pct after a violation makes a failing clause pass
    rather than fixing it, and the previous figure is what consumers were told."""
    before = _contract()
    after = _contract(version="1.1.0",
                      tolerates=[{"dependency": "d", "max_error_rate_pct": 15}])
    changes = D.compare(before, after)
    weakening = next(c for c in changes
                     if c["clause"] == "tolerates.d.max_error_rate_pct")
    assert weakening["weakens"] is True
    assert "5 -> 15" in weakening["detail"]


def test_lowering_an_availability_promise_is_weakening():
    before = _contract()
    after = _contract(provides={"availability_slo": 95.0, "latency_p99_ms": 500,
                                "graceful_degradation_modes": ["m1"]})
    changes = D.compare(before, after)
    assert next(c for c in changes
                if c["clause"] == "provides.availability_slo")["weakens"] is True


def test_raising_a_latency_promise_is_weakening():
    before = _contract()
    after = _contract(provides={"availability_slo": 99.5, "latency_p99_ms": 900,
                                "graceful_degradation_modes": ["m1"]})
    changes = D.compare(before, after)
    assert next(c for c in changes
                if c["clause"] == "provides.latency_p99_ms")["weakens"] is True


def test_tightening_a_promise_is_not_weakening():
    before = _contract()
    after = _contract(provides={"availability_slo": 99.9, "latency_p99_ms": 300,
                                "graceful_degradation_modes": ["m1"]})
    assert not any(c["weakens"] for c in D.compare(before, after))


def test_dropping_a_degradation_mode_is_weakening():
    before = _contract()
    after = _contract(provides={"availability_slo": 99.5, "latency_p99_ms": 500,
                                "graceful_degradation_modes": []})
    changes = D.compare(before, after)
    assert any(c["weakens"] and "m1" in c["detail"] for c in changes)


def test_removing_a_clause_entirely_is_weakening():
    before = _contract()
    after = _contract(tolerates=[])
    changes = D.compare(before, after)
    assert next(c for c in changes if c["kind"] == "removed")["weakens"] is True


def test_the_rendered_diff_names_the_owners_and_does_not_forbid():
    before = _contract()
    after = _contract(tolerates=[{"dependency": "d", "max_error_rate_pct": 15}])
    text = D.render("s", before, after, D.compare(before, after))
    assert "@o" in text
    assert "NOT blocked" in text
    assert "Is the change intentional?" in text


# --------------------------------------------------------------------------- #
# Units. A violated clause that names the WRONG mechanism is worse than one
# that names none: every number looks authoritative.
# --------------------------------------------------------------------------- #

def test_a_latency_clause_uses_the_latency_explainer(order_api):
    """The first version fed 1000 MILLISECONDS into the error-rate formula and
    reported a '1000.0% upstream error rate', '73000.0% of logical calls', and a
    remedy of '-8000.0% exposure'. Caught by reading the live GATE 9 report."""
    lines = R.explain_latency_amplification(order_api, "inventory-service", 1000.0, 2159.0)
    assert lines
    joined = " ".join(lines)
    assert "error rate" not in joined
    assert "1000.0%" not in joined and "73000" not in joined
    assert "ms" in joined


def test_no_mechanism_line_reports_a_percentage_above_one_hundred(order_api):
    """A blanket guard: no explanation may claim a probability over 100%.
    Anything that does is a unit error, whatever produced it."""
    import re
    for lines in (R.explain_latency_amplification(order_api, "inventory-service", 1000.0, 2159.0),
                  R.explain_retry_amplification(order_api, "inventory-service", 10.0, 12.1)):
        for line in lines:
            for pct in re.findall(r"(-?\d+(?:\.\d+)?)%", line):
                assert -0.01 <= float(pct) <= 100.0, f"impossible percentage in: {line}"


def test_latency_amplification_names_the_attempt_cost(order_api):
    """1000ms injected, 500ms wait: 1 attempt = 1000ms, 2 = 2500ms, 3 = 4000ms.
    The observed 2159ms must be attributed to the 2-attempt band."""
    lines = R.explain_latency_amplification(order_api, "inventory-service", 1000.0, 2159.0)
    joined = " ".join(lines)
    assert "2500ms" in joined
    assert "2 attempts" in joined
    assert "own retry policy" in joined.lower() or "OWN RETRY POLICY" in joined
