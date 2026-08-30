"""The workflow file, asserted as data.

The chaos gate's correctness depends on an ORDERING inside a YAML file, and a
YAML file is exactly the kind of thing someone reorders at 6pm while debugging
a timeout. These tests run in the `unit` job, in milliseconds, with no cluster —
so deleting the load-plane line fails on the developer's laptop rather than
producing a green badge over an unmeasured run.
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "chaos-gate.yml"


@pytest.fixture(scope="module")
def workflow():
    # `on:` is parsed by PyYAML 1.1 rules as the boolean True; harmless here,
    # but worth knowing before someone debugs a missing key for an hour.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def chaos_gate(workflow):
    return workflow["jobs"]["chaos-gate"]


def _steps(job) -> list[str]:
    return [s.get("name", s.get("uses", "")) for s in job["steps"]]


def _run_text(job) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


# --------------------------------------------------------------------------- #
# THE ORDERING. This is the single most important assertion in the suite.
# --------------------------------------------------------------------------- #

def test_the_load_plane_is_applied_before_the_experiment_runner(chaos_gate):
    """`kubectl apply -f k6/testrun-ci.yaml` must precede `ci_runner`.

    Reversed or removed, the gate injects faults into an idle system: every
    invariant reads an empty series, nothing can be falsified, and the job goes
    green having measured nothing. That is defect D1 reproduced inside the
    pipeline, where a green badge hides it.
    """
    runs = [s.get("run", "") for s in chaos_gate["steps"]]
    load_at = next(i for i, r in enumerate(runs) if "k6/testrun-ci.yaml" in r)
    runner_at = next(i for i, r in enumerate(runs) if "ci_runner" in r)
    assert load_at < runner_at, (
        "the k6 TestRun must be applied BEFORE ci_runner — a chaos gate with no "
        "load plane reports success without evidence")


def test_the_configmap_is_created_before_the_testrun_references_it(chaos_gate):
    """The TestRun mounts `ci-120rps`; applied first it schedules a runner pod
    that cannot start, and the wait loop reports LOAD_PLANE_ABSENT for a reason
    that is really an ordering bug."""
    text = _run_text(chaos_gate)
    assert text.index("create configmap ci-120rps") < text.index("k6/testrun-ci.yaml")


def test_the_ci_testrun_tag_matches_what_the_runner_reads(chaos_gate):
    """`arguments: --tag testrun=...` in the manifest and `TESTRUN` in the job
    env must agree. If they drift, every client query selects an empty series
    and every run is INVALID — with a cause nothing in the log names."""
    manifest = yaml.safe_load((REPO_ROOT / "k6" / "testrun-ci.yaml").read_text())
    tag = manifest["spec"]["arguments"].split("testrun=")[1].split()[0]
    assert tag == manifest["metadata"]["name"]
    assert chaos_gate["env"]["TESTRUN"] == tag


def test_the_ci_load_profile_clears_every_selected_floor(chaos_gate):
    """The k6 profile's arrival rate must exceed the strictest `min_rps_floor`
    of the experiments the gate runs. Below it, every gate run is INVALID —
    correct behaviour, useless gate — and the fix is a bigger runner, never a
    lower floor."""
    runner_step = next(s["run"] for s in chaos_gate["steps"] if "ci_runner" in s.get("run", ""))
    selected = runner_step.split("--experiments")[1].split()[0].split(",")

    floors = []
    for name in selected:
        spec = next(yaml.safe_load(p.read_text())["experiment"]
                    for p in (REPO_ROOT / "experiments").glob("*.yaml")
                    if yaml.safe_load(p.read_text())["experiment"]["name"] == name)
        floors.append(float(spec["min_rps_floor"]))

    profile = (REPO_ROOT / "profiles" / "steady_120rps.js").read_text()
    rate = int(profile.split("rate:")[1].split(",")[0].strip())
    assert rate > max(floors), f"k6 offers {rate} rps against a floor of {max(floors)}"


def test_the_gate_runs_an_experiment_that_asserts_the_circuit_breaker(chaos_gate):
    """GATE 7's premise: removing @CircuitBreaker from order-api's payment call
    must not be mergeable. That is only true if a gating experiment actually
    asserts on the breaker's published state."""
    runner_step = next(s["run"] for s in chaos_gate["steps"] if "ci_runner" in s.get("run", ""))
    selected = runner_step.split("--experiments")[1].split()[0].split(",")

    asserts_breaker = False
    for path in (REPO_ROOT / "experiments").glob("*.yaml"):
        spec = yaml.safe_load(path.read_text())["experiment"]
        if spec["name"] not in selected:
            continue
        metrics = {i["metric"] for i in spec["hypothesis"]["invariants"]}
        if spec.get("pattern_metric"):
            metrics.add(spec["pattern_metric"])
        asserts_breaker |= any(m.startswith("cb_") for m in metrics)
    assert asserts_breaker, "no selected experiment observes the circuit breaker"


def test_selected_experiments_include_at_least_one_gating_experiment(chaos_gate):
    """A gate composed entirely of advisory experiments reports verdicts and
    blocks nothing. Legitimate as a canary posture, wrong as a merge gate."""
    from src.orchestrator import ci_runner
    runner_step = next(s["run"] for s in chaos_gate["steps"] if "ci_runner" in s.get("run", ""))
    selected = [n for n in runner_step.split("--experiments")[1].split()[0].split(",")]
    entries, _min = ci_runner._load_registry()
    assert any(entries.get(n, {}).get("gating") for n in selected)


# --------------------------------------------------------------------------- #
# Job shape and dependencies
# --------------------------------------------------------------------------- #

def test_the_five_jobs_of_the_plan_shape_exist(workflow):
    assert set(workflow["jobs"]) == {
        "unit", "replay-eval", "contract-gate", "chaos-gate", "build-sign-deploy"}


def test_build_sign_deploy_needs_all_four_gates(workflow):
    assert set(workflow["jobs"]["build-sign-deploy"]["needs"]) == {
        "unit", "replay-eval", "contract-gate", "chaos-gate"}


def test_the_fast_gates_do_not_depend_on_the_cluster(workflow):
    """A developer must be able to fail cheaply before waiting on a kind
    cluster, or they learn to stop reading CI."""
    for job in ("unit", "replay-eval", "contract-gate"):
        assert "needs" not in workflow["jobs"][job]


def test_the_contract_gate_does_not_block_a_merge(workflow):
    """Tooling that forces a conversation beats tooling that forces a merge
    failure — the second kind gets bypassed, and then nobody has the
    conversation either."""
    assert workflow["jobs"]["contract-gate"]["continue-on-error"] is True


def test_the_chaos_gate_does_block_a_merge(workflow):
    assert workflow["jobs"]["chaos-gate"].get("continue-on-error") is not True


def test_the_scorer_has_its_own_coverage_floor(workflow):
    """`--cov-fail-under=80` must be scoped to the scorer, not to the whole
    framework — a repo-wide floor is satisfied by covering easy modules and
    says nothing about the one component whose bugs are invisible."""
    floor_steps = [s for s in workflow["jobs"]["unit"]["steps"]
                   if "--cov-fail-under=80" in s.get("run", "")]
    assert len(floor_steps) == 1
    assert "--cov=src/scoring" in floor_steps[0]["run"]


def test_a_failed_chaos_gate_dumps_the_cluster(chaos_gate):
    """A flaky chaos test that cannot be debugged gets disabled within a week,
    and then the gate protects nothing."""
    dump = [s for s in chaos_gate["steps"] if "cluster-info dump" in s.get("run", "")]
    assert dump and dump[0]["if"] == "failure()"


def test_the_load_plane_is_torn_down_on_every_exit_path(chaos_gate):
    """Cleanup never depends only on the happy path."""
    stop = [s for s in chaos_gate["steps"] if "delete testrun" in s.get("run", "")]
    assert stop and stop[0]["if"] == "always()"


def test_signing_never_happens_from_a_pull_request(workflow):
    """Signing an artifact built from an unmerged branch attests to something
    that is not on main; from a fork it signs someone else's code with this
    repository's identity."""
    condition = workflow["jobs"]["build-sign-deploy"]["if"]
    assert "github.event_name == 'push'" in condition
    assert "refs/heads/main" in condition


def test_cosign_signs_a_digest_not_a_tag(workflow):
    """A tag is mutable. Signing one attests to whatever it points at next."""
    text = _run_text(workflow["jobs"]["build-sign-deploy"])
    assert "cosign sign" in text
    assert "steps.build.outputs.digest" in text


# --------------------------------------------------------------------------- #
# Pinning. A document that mandates pinning while shipping `:latest` is the
# first thing a sharp reviewer notices.
# --------------------------------------------------------------------------- #

PINNED_FILES = [
    ".github/workflows/chaos-gate.yml",
    "k6/testrun-ci.yaml",
    "Dockerfile",
    "charts/chaosproof/values.yaml",
    "charts/chaosproof/templates/deployment.yaml",
    "charts/chaosproof/templates/cleanup-cronjob.yaml",
    "Makefile",
]


def _uncommented(relpath: str) -> str:
    """Comments are stripped before the check, because several of these files
    explain in prose why `:latest` is banned — and a lint that cannot tell a
    rule from its own documentation gets deleted rather than obeyed."""
    lines = []
    for line in (REPO_ROOT / relpath).read_text(encoding="utf-8").splitlines():
        head = line.split("#", 1)[0]
        if head.strip():
            lines.append(head)
    return "\n".join(lines)


@pytest.mark.parametrize("relpath", PINNED_FILES)
def test_no_floating_latest_tag_anywhere(relpath):
    assert ":latest" not in _uncommented(relpath), f"{relpath} ships a floating tag"


def test_litmus_is_pinned_to_an_exact_minor_not_3x(workflow):
    """Litmus ships monthly, so `3.x` is not a pin. 3.28.0 fixed a stale config
    leak across multiple probes of the same type — the safety plane uses several
    promProbes per experiment — and 3.29.0 fixed duplicate triggers under
    concurrent reconciles, which is what serial execution depends on."""
    version = workflow["env"]["LITMUS_CHART_VER"]
    assert version.count(".") == 2 and "x" not in version
    major, minor, _patch = version.split(".")
    assert (int(major), int(minor)) >= (3, 29)


def test_the_kind_node_matches_the_makefile(workflow):
    """CI running a different Kubernetes patch than `make kind-up` is how
    'works on my cluster' gets into a resilience project."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    pinned = next(line.split(":=")[1].strip() for line in makefile.splitlines()
                  if line.startswith("KIND_NODE"))
    assert workflow["env"]["KIND_NODE"] == pinned


def test_the_kind_node_is_at_least_1_35_for_cgroup_v2(workflow):
    """1.35+ requires cgroup v2, which is what makes throttle-ratio and PSI
    trustworthy. This project scores on those signals."""
    version = workflow["env"]["KIND_NODE"].split(":v")[1]
    major, minor = (int(p) for p in version.split(".")[:2])
    assert (major, minor) >= (1, 35)


@pytest.mark.parametrize("key", ["KPS_CHART_VER", "LITMUS_CHART_VER", "K6OP_CHART_VER"])
def test_every_chart_version_is_an_exact_pin(workflow, key):
    version = str(workflow["env"][key])
    assert version.count(".") == 2
    assert all(part.isdigit() for part in version.split("."))


# --------------------------------------------------------------------------- #
# Dependency names. The first CI run installed the PyPI project `celpy` instead
# of `cel-python` — different distributions, colliding import name — so the CEL
# evaluator was absent, policy.py failed closed, and all nine safety rules
# denied. The job failed for the right reason and said nothing useful about it.
# --------------------------------------------------------------------------- #

def _pyproject() -> str:
    return (REPO_ROOT / "chaos-framework" / "pyproject.toml").read_text(encoding="utf-8")


def test_the_cel_evaluator_is_declared_as_a_package_extra():
    """Declared once in metadata rather than spelled from memory in each
    workflow step."""
    text = _pyproject()
    assert "[project.optional-dependencies]" in text
    assert "cel-python" in text


def test_no_workflow_step_installs_the_wrong_distribution(workflow):
    """`celpy` is the import name and someone else's project on PyPI."""
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            if "pip install" in run:
                assert " celpy" not in run, (
                    "the distribution is `cel-python`; `pip install celpy` "
                    "installs an unrelated project")


def test_the_job_running_policy_eval_installs_the_policy_extra(workflow):
    """Otherwise the evaluator is missing, every rule fails closed, and the job
    fails for a reason its own output does not name."""
    for name, job in workflow["jobs"].items():
        runs = [s.get("run", "") for s in job["steps"]]
        if not any("evals.policy_eval" in r for r in runs):
            continue
        installs = " ".join(r for r in runs if "pip install" in r)
        assert "policy" in installs, f"job {name} runs policy_eval without the policy extra"


# --------------------------------------------------------------------------- #
# File modes. The repo is authored on Windows, which does not carry an
# executable bit, so a wrapper script committed here is mode 644 in the index
# and `./mvnw` on a Linux runner fails with "Permission denied" — after the
# cluster is already up. This test costs a millisecond and catches it before
# the eight-minute build does.
# --------------------------------------------------------------------------- #

import subprocess


def _index_mode(relpath: str) -> str:
    out = subprocess.run(["git", "ls-files", "-s", relpath], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=30).stdout
    assert out.strip(), f"{relpath} is not tracked by git"
    return out.split()[0]


@pytest.mark.parametrize("service", ["order-api", "payment-service", "inventory-service"])
def test_the_maven_wrapper_is_executable_in_the_index(service):
    """`git update-index --chmod=+x` is what records this; a local chmod on
    Windows does nothing and the mode a Linux runner sees comes from the index."""
    assert _index_mode(f"target-app/{service}/mvnw") == "100755", (
        f"target-app/{service}/mvnw is not executable in the git index — "
        "the chaos-gate job will fail with 'Permission denied' after paying for "
        "a full cluster bring-up")
