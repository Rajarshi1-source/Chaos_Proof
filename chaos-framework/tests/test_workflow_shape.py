"""The workflow file, asserted as data.

The chaos gate's correctness depends on an ORDERING inside a YAML file, and a
YAML file is exactly the kind of thing someone reorders at 6pm while debugging
a timeout. These tests run in the `unit` job, in milliseconds, with no cluster —
so deleting the load-plane line fails on the developer's laptop rather than
producing a green badge over an unmeasured run.
"""

import pathlib
import re

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

def test_the_jobs_of_the_plan_shape_exist(workflow):
    """The plan's §21.5 shape was five jobs:

        unit -> replay-eval -> contract-gate -> chaos-gate -> build-sign-deploy

    Phase 12 adds a sixth, `public-build`, for the two-tier deploy the plan
    asks for in the same phase. It is asserted here rather than left implicit
    because a job that silently stops existing is a gate that silently stops
    gating — which is the failure mode this whole file exists to catch.
    """
    assert set(workflow["jobs"]) == {
        "unit", "replay-eval", "contract-gate", "public-build", "chaos-gate",
        "build-sign-deploy"}


def test_build_sign_deploy_needs_every_gate(workflow):
    """Including `public-build`. Publishing an image whose dashboard ships the
    trigger surface is the exact thing the two-tier build exists to prevent, so
    the deploy job must not be reachable without it."""
    assert set(workflow["jobs"]["build-sign-deploy"]["needs"]) == {
        "unit", "replay-eval", "contract-gate", "public-build", "chaos-gate"}


def test_the_public_build_verifies_both_directions(workflow):
    """A grep for "no trigger surface" passes trivially against a misspelled
    marker, a wrong directory, or the residue of a failed build. Verifying that
    the INTERNAL build contains the surface is what makes the read-only check
    capable of failing — without it the gate is decorative."""
    steps = workflow["jobs"]["public-build"]["steps"]
    runs = " ".join(str(s.get("run", "")) for s in steps)
    assert "verify:internal" in runs
    assert "verify:readonly" in runs


def test_the_public_build_does_not_need_the_cluster(workflow):
    """It is a bundler assertion, not a chaos experiment. Making it wait on the
    kind cluster would put a one-minute check behind a thirty-minute one."""
    assert "needs" not in workflow["jobs"]["public-build"]


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
    assert dump and "failure()" in dump[0]["if"]


def test_the_load_plane_is_torn_down_on_every_exit_path(chaos_gate):
    """Cleanup never depends only on the happy path."""
    stop = [s for s in chaos_gate["steps"] if "delete testrun" in s.get("run", "")]
    assert stop and "always()" in stop[0]["if"]


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


def _pip_install_commands(dockerfile: pathlib.Path) -> list[str]:
    r"""Logical `pip install` commands, with `\` continuations joined.

    Scanning line by line does not work here, and the failure is silent: the
    package list wraps onto a continuation line containing no "pip install"
    text, so a per-line guard skips exactly the line the packages are on. The
    first version of this helper did that, passed, and would have missed the
    very `celpy` it was written to catch. Joining first is the whole point.
    """
    text = re.sub(r"\\\s*\r?\n", " ", dockerfile.read_text(encoding="utf-8"))
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "pip install" not in stripped:
            continue
        out.append(stripped)
    return out


def _dockerfiles() -> list[pathlib.Path]:
    root = pathlib.Path(__file__).resolve().parents[2]
    return [d for d in root.glob("**/Dockerfile")
            if "node_modules" not in d.parts and ".next" not in d.parts]


def test_no_dockerfile_installs_the_wrong_distribution():
    """The same check as above, against the IMAGE.

    d382314 fixed `celpy` -> `cel-python` in the workflow and added the guard
    above, but that guard only reads `workflow["jobs"]`. The Dockerfile kept the
    wrong distribution and shipped it in the signed image, where policy.py fails
    closed and denies all nine safety rules at runtime while CI stays green —
    invisible precisely because fail-closed looks like working software right up
    until something needs to be allowed.

    A guard covering one of the two places a dependency is named is a guard that
    teaches you the bug is fixed.
    """
    assert _dockerfiles(), "no Dockerfile found - this guard would be vacuous"
    for dockerfile in _dockerfiles():
        for cmd in _pip_install_commands(dockerfile):
            assert '"celpy' not in cmd and " celpy" not in cmd, (
                f"{dockerfile.name} installs `celpy`; the distribution is "
                f"`cel-python` and `celpy` is a different project on PyPI")


def test_the_cel_evaluator_is_installed_wherever_policy_is_evaluated():
    """The image evaluates the CEL policy at runtime, so it needs the evaluator
    as much as the policy-eval CI job does."""
    root = pathlib.Path(__file__).resolve().parents[2]
    cmds = _pip_install_commands(root / "Dockerfile")
    assert cmds, "no pip install found in the Dockerfile"
    assert any("cel-python" in c for c in cmds), (
        "the image never installs cel-python, so policy.py fails closed at "
        "runtime and every safety rule denies")


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


def test_the_kind_cli_version_is_pinned_not_defaulted(workflow, chaos_gate):
    """The kind CLI and the node image are a matched pair. Left to the action's
    default, v0.31.0 created a v1.36.1 cluster successfully and then failed on
    `kind load docker-image` with 'unknown containerd config version: 4' — a
    partially compatible CLI gets far enough to look fine, which is worse than
    an outright refusal."""
    step = next(s for s in chaos_gate["steps"] if s.get("uses", "").startswith("helm/kind-action"))
    assert "version" in step["with"], "the kind CLI version must be pinned explicitly"
    assert workflow["env"]["KIND_VERSION"].startswith("v")


def test_the_pinned_kind_cli_understands_the_node_images_containerd(workflow):
    """containerd config v4 landed in the 1.36 node images and needs kind
    >= 0.32.0 to read it."""
    major, minor = (int(p) for p in workflow["env"]["KIND_VERSION"].lstrip("v").split(".")[:2])
    assert (major, minor) >= (0, 32)


# --------------------------------------------------------------------------- #
# Capacity. The gate needs a runner it can actually fit on, and it has to say
# so BEFORE building a cluster rather than 14 minutes later via three opaque
# "Progress deadline exceeded" lines from `helm --wait`.
# --------------------------------------------------------------------------- #

def test_the_capacity_preflight_runs_before_anything_expensive(chaos_gate):
    steps = chaos_gate["steps"]
    guard = next(i for i, s in enumerate(steps) if "nproc" in s.get("run", ""))
    cluster = next(i for i, s in enumerate(steps)
                   if s.get("uses", "").startswith("helm/kind-action"))
    assert guard < cluster, "the capacity check must precede the cluster build"
    assert guard == 0, "it should be the very first step — it costs a second"


def test_the_capacity_preflight_fails_rather_than_skips(chaos_gate):
    """A chaos gate that quietly opts out on an undersized runner is a green
    badge over a run that measured nothing — the exact defect this pipeline
    exists to prevent."""
    guard = next(s for s in chaos_gate["steps"] if "nproc" in s.get("run", ""))
    assert "exit 1" in guard["run"]
    assert "RUNNER_TOO_SMALL" in guard["run"]
    assert "::error" in guard["run"]


def test_the_capacity_requirement_leaves_room_for_litmus_helper_pods(workflow):
    """Measured steady-state requests are 4000m. The requirement must exceed
    that by enough for the helper pods Litmus creates AT INJECTION TIME — a
    node sized to exactly 4000m schedules the cluster, starts the load plane,
    and then cannot place the first fault, 25 minutes in."""
    STEADY_STATE_REQUESTS_M = 4000
    headroom = int(workflow["env"]["REQUIRED_VCPU"]) * 1000 - STEADY_STATE_REQUESTS_M
    assert headroom >= 1500, (
        f"only {headroom}m of headroom above steady-state requests — not enough "
        "for Litmus helpers plus JVM burst under load")


def test_the_runner_label_is_overridable_without_editing_the_workflow(chaos_gate):
    assert "CHAOS_GATE_RUNNER" in str(chaos_gate["runs-on"])


def test_no_action_targets_the_deprecated_node_20_runtime(workflow):
    """actions/upload-artifact@v4 and azure/setup-helm@v4 are Node 20."""
    deprecated = {"actions/upload-artifact@v4", "azure/setup-helm@v4"}
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert step.get("uses") not in deprecated, f"{step.get('uses')} is Node 20"


def test_cleanup_is_gated_on_a_cluster_existing(chaos_gate):
    """Cleanup runs on every exit path THAT HAS A CLUSTER. With none — the
    capacity pre-flight aborting, say — `kubectl delete` hits localhost:8080 and
    fails with a connection-refused stack that reads like a real fault. Cleanup
    that cannot tell 'nothing to clean' from 'cleanup failed' trains people to
    ignore it."""
    for step in chaos_gate["steps"]:
        cond = str(step.get("if", ""))
        if "always()" in cond or "failure()" in cond:
            assert "steps.cluster.outcome" in cond, (
                f"step {step.get('name')!r} runs on abort paths without checking "
                "that a cluster was ever created")


def test_the_load_plane_teardown_still_runs_on_every_exit_path(chaos_gate):
    stop = next(s for s in chaos_gate["steps"] if "delete testrun" in s.get("run", ""))
    assert "always()" in stop["if"]


# --------------------------------------------------------------------------- #
# Phase 8 landed the last two hermetic gates. A gate that can silently skip
# itself is not a gate.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("module", ["evals.replay_eval", "evals.alert_rule_lint"])
def test_the_hermetic_evals_run_unconditionally(workflow, module):
    """These steps once carried an `if [ -f ... ]` guard that warned and passed
    while the module was still unwritten. Now that they exist, a missing file
    must fail the job — otherwise deleting an eval is a way to make CI green."""
    step = next(s for s in workflow["jobs"]["replay-eval"]["steps"]
                if module in s.get("run", ""))
    run = step["run"]
    assert "if [ -f" not in run, f"{module} can still skip itself"
    assert "::warning" not in run, f"{module} degrades to a warning instead of failing"


def test_every_eval_module_the_workflow_invokes_actually_exists():
    """The guards are gone, so a typo in a module name is now a hard CI failure
    rather than a silent skip. Catch it here, in milliseconds."""
    import re
    text = WORKFLOW.read_text(encoding="utf-8")
    for module in sorted(set(re.findall(r"python -m (evals\.[a-z_]+)", text))):
        path = REPO_ROOT / (module.replace(".", "/") + ".py")
        assert path.exists(), f"workflow runs {module} but {path} does not exist"


def test_the_replay_corpus_meets_its_minimum_size():
    """The plan requires >=20 bundles. A corpus that shrinks below that has
    lost regression cases, which is how a fixed defect comes back."""
    corpus = list((REPO_ROOT / "evals" / "corpus").glob("*.json"))
    assert len(corpus) >= 20, f"corpus has {len(corpus)} cases, minimum is 20"


def test_the_frozen_digest_set_covers_the_whole_corpus():
    """A corpus case with no frozen digest is unanchored: its behaviour could
    change without the determinism gate noticing."""
    import json
    digests = json.loads((REPO_ROOT / "evals" / "frozen_digests.json")
                         .read_text(encoding="utf-8"))
    from src.scoring import scorer
    frozen = digests.get(scorer.SCORER_VERSION, {})
    names = {p.stem for p in (REPO_ROOT / "evals" / "corpus").glob("*.json")}
    assert set(frozen) == names, (
        "frozen digests and corpus cases disagree: "
        f"unfrozen={sorted(names - set(frozen))} stale={sorted(set(frozen) - names)}")
