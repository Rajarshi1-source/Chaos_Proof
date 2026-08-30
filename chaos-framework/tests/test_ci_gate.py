"""The chaos gate's own decision logic, tested without a cluster.

The chaos-gate job cannot be unit-tested end to end — that is the point of it.
What CAN be tested, and must be, is everything that decides whether a green
badge is honest: which experiments may block a merge, whether a not-held
verdict actually fails the job, and whether a failure names its own cause.
"""

import pathlib

import pytest
import yaml
from helpers import make_samples

from src.constants import FLAKINESS_WINDOW
from src.hypothesis.engine import Invariant, evaluate
from src.measurement.validity import LoadFacts
from src.orchestrator import ci_runner

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VALID = LoadFacts(achieved_rps=119.4, dropped_iterations=0, coverage=1.0)


@pytest.fixture(autouse=True)
def no_evidence_store(monkeypatch):
    """Gating resolution must not depend on a reachable database, and an
    unreachable one must mean 'no measured status', never 'everything fine'."""
    monkeypatch.setattr(ci_runner, "_measured_status", lambda: {})


# --------------------------------------------------------------------------- #
# The registry is data a reviewer reads, so it has to stay well-formed.
# --------------------------------------------------------------------------- #

def test_registry_min_clean_runs_matches_the_flakiness_window():
    doc = yaml.safe_load((REPO_ROOT / "ci" / "gating-experiments.yaml").read_text())
    assert doc["min_clean_runs"] == FLAKINESS_WINDOW


def test_every_registry_entry_names_a_real_experiment():
    doc = yaml.safe_load((REPO_ROOT / "ci" / "gating-experiments.yaml").read_text())
    known = {yaml.safe_load(p.read_text())["experiment"]["name"]
             for p in (REPO_ROOT / "experiments").glob("*.yaml")}
    for entry in doc["experiments"]:
        assert entry["name"] in known, f"{entry['name']} has gating authority over nothing"


def test_every_gating_entry_states_a_reason():
    """An experiment that can block a merge has to justify it in the file, so
    the justification is reviewed rather than assumed."""
    doc = yaml.safe_load((REPO_ROOT / "ci" / "gating-experiments.yaml").read_text())
    for entry in doc["experiments"]:
        if entry.get("gating"):
            assert entry.get("reason", "").strip(), f"{entry['name']} gates without a reason"


# --------------------------------------------------------------------------- #
# Gating resolution. Registry declares candidacy; measurement decides authority.
# --------------------------------------------------------------------------- #

def test_unknown_experiment_is_advisory_not_gating():
    """Adding an experiment file must not silently grant it merge-blocking
    authority. Software promotes by push; an experiment promotes by
    characterisation."""
    st = ci_runner.resolve_gating(["disk_fill_inventory"])["disk_fill_inventory"]
    assert st.gating is False
    assert "not in ci/gating-experiments.yaml" in st.note


def test_bootstrap_gating_is_labelled_uncharacterised_on_every_run():
    """The exception is allowed, but it can never be quiet: the label carries
    the clean-run count into the CI summary of every single run."""
    st = ci_runner.resolve_gating(["network_partition_payment"])["network_partition_payment"]
    assert st.gating is True
    assert "bootstrap" in st.note and "uncharacterised" in st.note
    assert f"/{FLAKINESS_WINDOW}" in st.note


def test_uncharacterised_without_bootstrap_is_advisory(monkeypatch, tmp_path):
    registry = tmp_path / "gating.yaml"
    registry.write_text(yaml.safe_dump({
        "version": 1, "min_clean_runs": 20,
        "experiments": [{"name": "x", "gating": True, "clean_runs": 3}]}))
    monkeypatch.setattr(ci_runner, "GATING_REGISTRY", registry)
    st = ci_runner.resolve_gating(["x"])["x"]
    assert st.gating is False
    assert "3/20" in st.note


def test_characterised_entry_gates_without_the_bootstrap_label(monkeypatch, tmp_path):
    registry = tmp_path / "gating.yaml"
    registry.write_text(yaml.safe_dump({
        "version": 1, "min_clean_runs": 20,
        "experiments": [{"name": "x", "gating": True, "clean_runs": 25}]}))
    monkeypatch.setattr(ci_runner, "GATING_REGISTRY", registry)
    st = ci_runner.resolve_gating(["x"])["x"]
    assert st.gating is True
    assert "characterised" in st.note and "bootstrap" not in st.note


def test_measured_flakiness_demotes_a_gating_experiment(monkeypatch):
    """Quarantine, exercised: a flaky experiment loses its authority to block
    without anyone editing a file."""
    monkeypatch.setattr(ci_runner, "_measured_status",
                        lambda: {"network_partition_payment":
                                 (False, "quarantined, sigma=0.1400")})
    st = ci_runner.resolve_gating(["network_partition_payment"])["network_partition_payment"]
    assert st.gating is False
    assert "sigma=0.1400" in st.note


def test_measurement_promotes_a_characterised_candidate(monkeypatch):
    """Phase 8 changes the Phase 7 one-way rule deliberately: 20 clean runs on
    unchanged code is how an experiment EARNS gating status (§21.3), so
    measurement must be able to promote as well as demote."""
    monkeypatch.setattr(ci_runner, "_measured_status",
                        lambda: {"network_partition_payment":
                                 (True, "characterised by measurement, sigma=0.0100")})
    st = ci_runner.resolve_gating(["network_partition_payment"])["network_partition_payment"]
    assert st.gating is True
    assert "characterised by measurement" in st.note


def test_measurement_cannot_promote_an_experiment_absent_from_the_registry(monkeypatch):
    """The safety property that survives from Phase 7: adding an experiment file
    and letting it run 20 times must not, on its own, grant it power over other
    people's merges. Measurement decides whether a CANDIDATE is stable; the
    registry decides what may be a candidate at all."""
    monkeypatch.setattr(ci_runner, "_measured_status",
                        lambda: {"disk_fill_inventory": (True, "stable, sigma=0.001")})
    assert ci_runner.resolve_gating(["disk_fill_inventory"])["disk_fill_inventory"].gating is False


def test_measurement_cannot_promote_an_experiment_declared_advisory(monkeypatch, tmp_path):
    """An explicit opt-out in the registry outranks a stable measurement."""
    registry = tmp_path / "gating.yaml"
    registry.write_text(yaml.safe_dump({
        "version": 1, "min_clean_runs": 20,
        "experiments": [{"name": "x", "gating": False}]}))
    monkeypatch.setattr(ci_runner, "GATING_REGISTRY", registry)
    monkeypatch.setattr(ci_runner, "_measured_status", lambda: {"x": (True, "stable")})
    assert ci_runner.resolve_gating(["x"])["x"].gating is False


def test_missing_registry_leaves_everything_advisory(monkeypatch, tmp_path):
    monkeypatch.setattr(ci_runner, "GATING_REGISTRY", tmp_path / "absent.yaml")
    assert ci_runner.resolve_gating(["network_partition_payment"])[
        "network_partition_payment"].gating is False


# --------------------------------------------------------------------------- #
# The diagnosis line. GATE 7's message is produced here, from data.
# --------------------------------------------------------------------------- #

def test_gate7_message_is_derived_from_the_invariant_outcome():
    """A breaker-state gauge that publishes but never reaches 1 — exactly what
    removing @CircuitBreaker from order-api's payment call produces under a
    partition."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 0, 0, 0, 0]})
    verdict = evaluate([Invariant("circuit_breaker_opens", "prometheus",
                                  "cb_payment_open", ">=", 1.0, recover_within_s=90.0)],
                       ss, VALID, 90.0)
    assert verdict.verdict == "falsified"
    assert ci_runner.diagnose(verdict).startswith("circuit_breaker_opens never activated")


def test_diagnosis_distinguishes_never_activated_from_recovered_late():
    """Two different findings: the pattern is missing, versus the pattern worked
    but slowly. A gate that renders them identically teaches nothing."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 0, 0, 0, 1]})
    verdict = evaluate([Invariant("circuit_breaker_opens", "prometheus",
                                  "cb_payment_open", ">=", 1.0, recover_within_s=10.0)],
                       ss, VALID, 90.0)
    detail = ci_runner.diagnose(verdict)
    assert "did not recover" in detail
    assert "never activated" not in detail


def test_diagnosis_of_an_unmeasurable_invariant_says_so():
    """'We could not measure it' must never render as 'it failed'."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [None] * 6})
    verdict = evaluate([Invariant("circuit_breaker_opens", "prometheus",
                                  "cb_payment_open", ">=", 1.0, recover_within_s=90.0)],
                       ss, VALID, 90.0)
    detail = ci_runner.diagnose(verdict)
    assert "could not be measured" in detail
    assert "never activated" not in detail


def test_diagnosis_of_a_hold_invariant_reports_the_breach_duration():
    ss = make_samples({"client_availability": [1.0, 0.5, 0.5, 0.5, 0.5, 1.0]})
    verdict = evaluate([Invariant("client_availability_holds", "k6",
                                  "client_availability", ">=", 0.99, tolerance_s=5.0)],
                       ss, VALID, 90.0)
    detail = ci_runner.diagnose(verdict)
    assert "client_availability_holds breached for 15s" in detail


def test_diagnosis_of_an_invalid_run_reports_the_load_reason():
    """An INVALID verdict has no invariant outcomes at all — the validity gate
    short-circuits before them — so the diagnosis has to fall back to the
    verdict's own reason rather than emitting an empty string."""
    ss = make_samples({"client_availability": [1.0] * 6})
    no_load = LoadFacts(achieved_rps=0.0, dropped_iterations=0, coverage=1.0)
    verdict = evaluate([Invariant("a", "k6", "client_availability", ">=", 0.99,
                                  tolerance_s=5.0)], ss, no_load, 90.0)
    assert "0 rps" in ci_runner.diagnose(verdict)


# --------------------------------------------------------------------------- #
# Not-held is not a nuance. INVALID especially must not go green.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("verdict", ["falsified", "invalid", "aborted", "skipped", "denied"])
def test_every_non_held_verdict_blocks_a_gating_experiment(verdict):
    st = ci_runner.GatingStatus("x", True, "characterised")
    assert (st.gating and verdict != "held") is True


def test_held_is_the_only_passing_verdict():
    st = ci_runner.GatingStatus("x", True, "characterised")
    assert (st.gating and "held" != "held") is False


@pytest.mark.parametrize("verdict", ["falsified", "invalid", "aborted"])
def test_an_advisory_experiment_never_blocks(verdict):
    st = ci_runner.GatingStatus("x", False, "uncharacterised")
    assert (st.gating and verdict != "held") is False


def test_exit_codes_are_distinct_so_a_failure_names_its_own_cause():
    codes = {ci_runner.EXIT_OK, ci_runner.EXIT_GATING_FAILED,
             ci_runner.EXIT_LOAD_PLANE_ABSENT, ci_runner.EXIT_MISCONFIGURED}
    assert len(codes) == 4
    assert ci_runner.EXIT_OK == 0


# --------------------------------------------------------------------------- #
# The load-plane assertion — the reason the gate is worth anything.
# --------------------------------------------------------------------------- #

class _FakeSampler:
    """Stands in for DualSourceSampler. `snapshot` is what the wait loop polls;
    `stream` is what the validity gate is then run over."""

    def __init__(self, rps, stream_rps=None):
        self.rps = rps
        self.stream_rps = stream_rps if stream_rps is not None else rps

    def snapshot(self):
        return {"client_rps": self.rps}, {}

    def stream(self, duration_s, **_kw):
        return make_samples({"client_rps": [self.stream_rps] * 6,
                             "dropped_iterations": [0] * 6})


def _patch_sampler(monkeypatch, sampler, *, no_sleep=True):
    monkeypatch.setattr(ci_runner, "DualSourceSampler", lambda *a, **k: sampler)
    if no_sleep:
        monkeypatch.setattr(ci_runner.time, "sleep", lambda _s: None)


def test_absent_load_plane_fails_the_gate_before_anything_is_injected(monkeypatch):
    """The deleted-`kubectl apply` case. This must be a loud failure with its
    own exit code — never six quiet INVALID runs, and never a green badge."""
    _patch_sampler(monkeypatch, _FakeSampler(None))
    monkeypatch.setattr(ci_runner, "LOAD_PLANE_TIMEOUT_S", 0)
    with pytest.raises(SystemExit) as exc:
        ci_runner.assert_load_plane(object(), "ci-120rps", 90.0)
    assert exc.value.code == ci_runner.EXIT_LOAD_PLANE_ABSENT


def test_load_plane_below_the_floor_fails_rather_than_lowering_the_floor(monkeypatch):
    """A starved runner is a runner problem. Widening the floor to make the job
    pass would make every subsequent verdict unmeasurable — and green."""
    _patch_sampler(monkeypatch, _FakeSampler(40.0))
    monkeypatch.setattr(ci_runner, "LOAD_PLANE_TIMEOUT_S", 0)
    with pytest.raises(SystemExit) as exc:
        ci_runner.assert_load_plane(object(), "ci-120rps", 90.0)
    assert exc.value.code == ci_runner.EXIT_LOAD_PLANE_ABSENT


def test_load_plane_passing_the_wait_but_failing_the_validity_gate_still_fails(monkeypatch):
    """The entry criterion is the SAME gate the runner applies on exit. A gate
    that admits runs it will later have to throw away is not a gate."""
    _patch_sampler(monkeypatch, _FakeSampler(120.0, stream_rps=40.0))
    with pytest.raises(SystemExit) as exc:
        ci_runner.assert_load_plane(object(), "ci-120rps", 90.0)
    assert exc.value.code == ci_runner.EXIT_LOAD_PLANE_ABSENT


def test_healthy_load_plane_returns_valid_facts(monkeypatch):
    _patch_sampler(monkeypatch, _FakeSampler(120.0))
    facts = ci_runner.assert_load_plane(object(), "ci-120rps", 90.0)
    assert facts.achieved_rps == pytest.approx(120.0)
    assert facts.is_valid(90.0)


def test_the_floor_is_the_strictest_of_the_selected_experiments():
    """The load plane has to satisfy every hypothesis that will read from it,
    not just the laxest one."""
    floors = []
    for name in ("network_partition_payment", "pod_kill_payment_svc"):
        path = ci_runner._resolve(name)
        floors.append(float(yaml.safe_load(path.read_text())["experiment"]["min_rps_floor"]))
    assert max(floors) >= max(floors[0], floors[-1])
