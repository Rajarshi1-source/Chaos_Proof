"""The CI entry point for the chaos gate (§21.5).

Invoked by `.github/workflows/chaos-gate.yml` as

    python -m src.orchestrator.ci_runner \
        --experiments network_partition_payment,pod_kill_payment_svc --override

(the plan writes this as `chaos_framework.orchestrator.ci_runner`; the package
is rooted at `chaos-framework/` and imported as `src`, matching the Makefile and
`chaosctl`. Same module, real import path.)

Three things live here rather than in the workflow YAML, because a rule written
in YAML is a rule someone deletes while debugging:

  1. THE LOAD-PLANE ASSERTION RUNS FIRST, before any pre-flight, before any
     injection. `kubectl apply -f k6/testrun-ci.yaml` is the most important line
     in the workflow, and its absence must be a loud failure rather than a green
     badge. If the line is deleted, the assertion here fails with
     LOAD_PLANE_ABSENT and names the missing series — instead of six experiments
     quietly returning INVALID, or worse, an empty invariant reading as a pass.
     This is defect D1 reproduced inside the pipeline, and inside the pipeline is
     the hardest place to notice it.

  2. GATING STATUS is resolved per experiment. An advisory experiment reports its
     verdict and never blocks the merge; only a characterised gating experiment
     can. Flakiness data may demote; it may never promote.

  3. NOT-HELD IS NOT A NUANCE. On a gating experiment, every verdict other than
     `held` fails the job — falsified, invalid, aborted, skipped and denied
     alike. INVALID especially: "we could not measure it" must never be the
     reason a merge went green.
"""

import argparse
import os
import pathlib
import sys
import time

import yaml

from .. import queries
from ..constants import FLAKINESS_WINDOW
from ..integrations.prometheus import PrometheusClient
from ..measurement.sampler import DualSourceSampler
from ..measurement.validity import LoadFacts
from . import runner
from .runner import PreflightSkip

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
GATING_REGISTRY = REPO_ROOT / "ci" / "gating-experiments.yaml"

# Long enough for the k6 Operator to schedule the runner pod, pull grafana/k6,
# and for two 5s scrapes plus a 30s rate window to produce a non-zero rate.
# Shorter than this and a healthy load plane reads as absent.
LOAD_PLANE_TIMEOUT_S = 240
LOAD_PLANE_SAMPLE_S = 60

BANNER = {
    "held": "HYPOTHESIS_HELD",
    "falsified": "HYPOTHESIS_FALSIFIED",
    "invalid": "INVALID",
    "aborted": "ABORTED",
    "skipped": "SKIPPED",
    "denied": "DENIED",
}

EXIT_OK = 0
EXIT_GATING_FAILED = 1
EXIT_LOAD_PLANE_ABSENT = 2
EXIT_MISCONFIGURED = 3


# --------------------------------------------------------------------------- #
# GitHub Actions surface. Plain text when run locally.
# --------------------------------------------------------------------------- #

def _annotate(level: str, title: str, message: str) -> None:
    """A gate failure has to name its own cause in the PR's Files-changed view,
    not only in 400 lines of job log."""
    one_line = message.replace("\n", " ").replace("::", ":")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title={title}::{one_line}")
    else:
        print(f"[{level.upper()}] {title}: {one_line}")


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Gating status
# --------------------------------------------------------------------------- #

class GatingStatus:
    def __init__(self, name: str, gating: bool, note: str):
        self.name = name
        self.gating = gating
        self.note = note

    def label(self) -> str:
        return ("GATING" if self.gating else "advisory") + (f" ({self.note})" if self.note else "")


def _load_registry() -> tuple[dict, int]:
    if not GATING_REGISTRY.exists():
        return {}, FLAKINESS_WINDOW
    doc = yaml.safe_load(GATING_REGISTRY.read_text()) or {}
    entries = {e["name"]: e for e in doc.get("experiments", [])}
    return entries, int(doc.get("min_clean_runs", FLAKINESS_WINDOW))


def _measured_demotions() -> dict[str, str]:
    """Phase 8's automatic quarantine, read one-way: an experiment recorded as
    non-gating in `experiment_flakiness` is demoted here regardless of what the
    registry declares. Nothing in this function can promote an experiment.

    The evidence store is not reachable from every CI job, and that must not be
    the thing that decides whether a merge is blocked — so an unreachable store
    means "no demotions known", never "everything is fine".
    """
    try:
        import psycopg

        from ..db import DSN
        with psycopg.connect(DSN, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT et.name, f.gating, f.score_stddev
                     FROM experiment_flakiness f
                     JOIN experiment_types et ON et.id = f.experiment_type_id""")
            rows = cur.fetchall()
    except Exception as exc:                                   # noqa: BLE001
        print(f"note       : flakiness data unreachable ({type(exc).__name__}); "
              "no measured demotions applied")
        return {}

    demoted = {}
    for name, gating, stddev in rows:
        if not gating:
            sigma = "unknown" if stddev is None else f"{float(stddev):.4f}"
            demoted[name] = f"quarantined by flakiness measurement, sigma={sigma}"
    return demoted


def resolve_gating(names: list[str]) -> dict[str, GatingStatus]:
    entries, min_clean = _load_registry()
    demotions = _measured_demotions()
    out: dict[str, GatingStatus] = {}

    for name in names:
        entry = entries.get(name)
        if entry is None:
            out[name] = GatingStatus(name, False, "not in ci/gating-experiments.yaml")
            continue
        if name in demotions:
            out[name] = GatingStatus(name, False, demotions[name])
            continue
        if not entry.get("gating"):
            out[name] = GatingStatus(name, False, "declared advisory")
            continue

        clean = int(entry.get("clean_runs", 0))
        if clean >= min_clean:
            out[name] = GatingStatus(name, True, f"characterised, {clean}/{min_clean}")
        elif entry.get("bootstrap"):
            # Declared exception, surfaced on every run rather than defaulted to.
            out[name] = GatingStatus(
                name, True, f"bootstrap, uncharacterised — {clean}/{min_clean} clean runs")
        else:
            out[name] = GatingStatus(
                name, False, f"uncharacterised — {clean}/{min_clean} clean runs")
    return out


# --------------------------------------------------------------------------- #
# 1. The load-plane assertion
# --------------------------------------------------------------------------- #

def assert_load_plane(prom: PrometheusClient, testrun: str, floor: float) -> LoadFacts:
    """Runs BEFORE anything else. Returns valid LoadFacts or raises SystemExit.

    Deliberately the same validity gate the runner applies to a finished
    experiment (`LoadFacts.is_valid`), not a looser "is anything there" check —
    a gate whose entry criterion is weaker than its exit criterion lets in runs
    it will later have to throw away.
    """
    sampler = DualSourceSampler(prom, queries.client_queries(testrun),
                                queries.server_queries())

    print(f"load plane : waiting for testrun={testrun} to reach {floor:.0f} rps "
          f"(timeout {LOAD_PLANE_TIMEOUT_S}s)")
    deadline = time.time() + LOAD_PLANE_TIMEOUT_S
    seen_any = False
    while time.time() < deadline:
        client, _server = sampler.snapshot()
        rps = client.get("client_rps")
        if rps is not None:
            seen_any = True
            print(f"             client_rps={rps:.1f}")
            if rps >= floor:
                break
        else:
            print("             client_rps=- (no k6 series yet)")
        time.sleep(10)
    else:
        _fail_load_plane(seen_any, testrun, floor)

    print(f"load plane : sampling {LOAD_PLANE_SAMPLE_S}s through the validity gate")
    samples = sampler.stream(duration_s=LOAD_PLANE_SAMPLE_S)
    facts = LoadFacts(
        achieved_rps=samples.mean_client("client_rps"),
        dropped_iterations=samples.windowed_increase("dropped_iterations"),
        coverage=samples.coverage(),
    )
    if not facts.is_valid(floor):
        _annotate("error", "chaos-gate: load plane",
                  f"LOAD_PLANE_INVALID — {facts.invalidity_reason}. The gate refuses to "
                  "inject faults it cannot measure; a chaos gate with no load plane "
                  "reports success without evidence.")
        _summary(["## chaos-gate: LOAD PLANE INVALID", "",
                  f"`{facts.invalidity_reason}`", "",
                  "No experiment ran. Nothing was injected."])
        raise SystemExit(EXIT_LOAD_PLANE_ABSENT)

    print(f"load plane : VALID — {facts.achieved_rps:.1f} rps, "
          f"dropped {facts.dropped_iterations:.0f}, coverage {facts.coverage:.0%}")
    return facts


def _fail_load_plane(seen_any: bool, testrun: str, floor: float) -> None:
    if seen_any:
        detail = (f"the k6 TestRun {testrun!r} is running but never reached the "
                  f"{floor:.0f} rps validity floor within {LOAD_PLANE_TIMEOUT_S}s — the "
                  "runner is starved, not the target. Lower the floor and the gate "
                  "measures nothing; give the job a bigger runner instead.")
    else:
        detail = (f"no k6 client series for testrun={testrun!r} at all. Did "
                  "`kubectl apply -f k6/testrun-ci.yaml` run before this step? A chaos "
                  "gate with no load plane injects faults into an idle system, every "
                  "invariant reads an empty series, and the badge goes green having "
                  "measured nothing.")
    _annotate("error", "chaos-gate: load plane", f"LOAD_PLANE_ABSENT — {detail}")
    _summary(["## chaos-gate: LOAD PLANE ABSENT", "", detail, "",
              "No experiment ran. Nothing was injected."])
    raise SystemExit(EXIT_LOAD_PLANE_ABSENT)


# --------------------------------------------------------------------------- #
# 2. Diagnosis — the failure has to name itself
# --------------------------------------------------------------------------- #

def diagnose(verdict) -> str:
    """One line a developer can act on, derived from the invariant outcomes
    rather than from the banner. GATE 7's message —
    `circuit_breaker_opens never activated` — is this function reading a
    recovery invariant whose metric never once crossed its threshold.
    """
    bad = [o for o in verdict.outcomes if o.outcome in ("falsified", "invalid")]
    if not bad:
        return verdict.reason or ""

    parts = []
    for o in bad:
        kind = o.evidence.get("kind", "")
        if o.outcome == "invalid":
            parts.append(f"{o.name} could not be measured "
                         f"({o.evidence.get('reason', 'no samples')})")
        elif kind == "activation" and o.evidence.get("note") == "never activated":
            worst = "-" if o.worst_value is None else f"{o.worst_value:.4g}"
            parts.append(f"{o.name} never activated (stayed at {worst}, needed "
                         f"{o.threshold:.4g} within "
                         f"{o.evidence.get('deadline_s', 0):.0f}s)")
        elif kind == "activation":
            parts.append(f"{o.name} activated late "
                         f"({o.evidence.get('activated_after_s', 0):.0f}s, deadline "
                         f"{o.evidence.get('deadline_s', 0):.0f}s)")
        elif kind == "recovery" and o.evidence.get("note") == "never recovered":
            # The signal never once satisfied its comparator in the whole window.
            # For a breaker-state gauge that reads: the breaker never opened.
            worst = "-" if o.worst_value is None else f"{o.worst_value:.4g}"
            parts.append(f"{o.name} never activated (stayed at {worst}, needed "
                         f"{o.threshold:.4g} within "
                         f"{o.evidence.get('deadline_s', 0):.0f}s)")
        elif kind == "recovery":
            parts.append(f"{o.name} did not recover within its "
                         f"{o.evidence.get('deadline_s', 0):.0f}s deadline "
                         f"(breached {o.breached_for_s:.0f}s)")
        else:
            worst = "-" if o.worst_value is None else f"{o.worst_value:.4g}"
            parts.append(f"{o.name} breached for {o.breached_for_s:.0f}s "
                         f"(worst {worst} vs {o.threshold:.4g})")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# 3. The gate
# --------------------------------------------------------------------------- #

def _resolve(name: str) -> pathlib.Path:
    for path in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())["experiment"]
        if spec["name"] == name:
            return path
    raise SystemExit(EXIT_MISCONFIGURED)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(prog="ci_runner")
    ap.add_argument("--experiments", required=True,
                    help="comma-separated experiment names, run serially in order")
    ap.add_argument("--override", action="store_true",
                    help="satisfy require_override policy rules. In CI this is an "
                         "explicit, recorded decision about a disposable kind cluster "
                         "with no users — never a default")
    args = ap.parse_args(argv)

    names = [n.strip() for n in args.experiments.split(",") if n.strip()]
    if not names:
        _annotate("error", "chaos-gate", "--experiments was empty")
        return EXIT_MISCONFIGURED

    specs = {}
    for name in names:
        path = _resolve(name)
        specs[name] = (path, yaml.safe_load(path.read_text())["experiment"])

    status = resolve_gating(names)
    prom_url = os.environ.get("PROM_URL", "http://localhost:19090")
    am_url = os.environ.get("ALERTMANAGER_URL", "http://localhost:19093")
    testrun = os.environ.get("TESTRUN", "ci-120rps")
    prom = PrometheusClient(prom_url)

    print("chaos-gate : " + ", ".join(f"{n} [{status[n].label()}]" for n in names))
    if not any(s.gating for s in status.values()):
        # Advisory-only is a legitimate posture (a canary deploy runs this way),
        # but it must never be mistaken for a gate that passed.
        _annotate("warning", "chaos-gate",
                  "every selected experiment is advisory — this run reports verdicts "
                  "but cannot block a merge")

    # The floor is the STRICTEST of the selected experiments: the load plane has
    # to satisfy every hypothesis that will read from it, not just the laxest.
    floor = max(float(spec["min_rps_floor"]) for _p, spec in specs.values())
    assert_load_plane(prom, testrun, floor)

    results: list[dict] = []
    failed_gating = False

    for name in names:
        path, spec = specs[name]
        st = status[name]
        print()
        print(f"=== {name}  [{st.label()}]  fault {spec['litmus_fault']} "
              f"{spec['fault_duration_s']}s + recovery {spec['recovery_window_s']}s")
        try:
            r = runner.run(path, prom_url, testrun, am_url,
                           require_override=args.override)
            verdict_name = r["verdict"].verdict
            detail = diagnose(r["verdict"])
            score = r["score"]
            score_text = ("none" if score.score is None
                          else f"{score.score:.4f} ({score.status})")
            print(f"    execution #{r['execution_id']}  verdict "
                  f"{BANNER.get(verdict_name, verdict_name.upper())}  score {score_text}")
        except PreflightSkip as e:
            verdict_name, detail = e.verdict, e.reason
            score_text = "none"
            runner.record_refusal(spec, e.verdict, e.reason, e.evidence)
            print(f"    REFUSED  {BANNER.get(verdict_name, verdict_name.upper())} — {detail}")

        banner = BANNER.get(verdict_name, verdict_name.upper())
        blocking = st.gating and verdict_name != "held"
        failed_gating = failed_gating or blocking
        results.append({"name": name, "status": st, "verdict": verdict_name,
                        "banner": banner, "detail": detail, "score": score_text,
                        "blocking": blocking})

        if blocking:
            _annotate("error", f"chaos-gate: {name}", f"{banner} — {detail}")
        elif verdict_name != "held":
            _annotate("warning", f"chaos-gate: {name} (advisory)", f"{banner} — {detail}")

    _write_summary(results)

    print()
    if failed_gating:
        names_failed = [r["name"] for r in results if r["blocking"]]
        print(f"chaos-gate : FAILED — {', '.join(names_failed)}")
        return EXIT_GATING_FAILED
    print("chaos-gate : PASSED")
    return EXIT_OK


def _write_summary(results: list[dict]) -> None:
    lines = ["## chaos-gate", "",
             "| experiment | authority | verdict | score | detail |",
             "|---|---|---|---|---|"]
    for r in results:
        mark = "❌" if r["blocking"] else ("✅" if r["verdict"] == "held" else "⚠️")
        detail = (r["detail"] or "").replace("|", "\\|")
        lines.append(f"| `{r['name']}` | {r['status'].label()} | {mark} {r['banner']} "
                     f"| {r['score']} | {detail} |")
    lines += ["",
              "⚠️ marks an advisory result: reported, not blocking. An advisory "
              "experiment earns the authority to block a merge through flakiness "
              "characterisation, never by being added to this list."]
    _summary(lines)


if __name__ == "__main__":
    raise SystemExit(main())
