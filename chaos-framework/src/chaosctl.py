"""chaosctl — the CLI surface (mlops-quality reference §5).

Phase 3 shipped `run` and `list`; Phase 8 adds the quality surface —
`flakiness`, `epoch`, and `retro-score`. plan/replay/freeze/bisect land with
their phases.

Usage:
    python -m src.chaosctl run pod_kill_payment_svc
    python -m src.chaosctl list
    python -m src.chaosctl flakiness [--experiment X] [--apply]
    python -m src.chaosctl epoch [--show | --open --reason "..."]
    python -m src.chaosctl retro-score --last N --reason "..." [--apply]
Env:
    PROM_URL (default http://localhost:19090), TESTRUN (default steady-120rps)
    CHAOSPROOF_DB, CHAOSPROOF_SLACK_WEBHOOK, GITHUB_TOKEN
"""

import argparse
import os
import pathlib
import sys

import yaml

from .orchestrator import runner
from .orchestrator.runner import PreflightSkip

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPERIMENTS_DIR = REPO_ROOT / "experiments"

VERDICT_BANNER = {
    "held": "HYPOTHESIS_HELD",
    "falsified": "HYPOTHESIS_FALSIFIED",
    "invalid": "INVALID",
    "aborted": "ABORTED",
    "skipped": "SKIPPED",
    "denied": "DENIED",
}
EXIT_CODES = {"held": 0, "falsified": 1, "invalid": 2,
              "aborted": 3, "skipped": 4, "denied": 5}


def _resolve(name: str) -> pathlib.Path:
    for path in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())["experiment"]
        if spec["name"] == name:
            return path
    raise SystemExit(f"no experiment named {name!r} in {EXPERIMENTS_DIR}. "
                     f"Try: python -m src.chaosctl list")


def cmd_list(_args) -> int:
    print(f"{'NAME':32} {'FAULT':22} {'FLOOR':>6}  HYPOTHESIS")
    for path in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())["experiment"]
        print(f"{spec['name']:32} {spec['litmus_fault']:22} "
              f"{spec['min_rps_floor']:>6.0f}  v{spec['hypothesis']['version']} "
              f"({len(spec['hypothesis']['invariants'])} invariants)")
    return 0


def cmd_run(args) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    spec_path = _resolve(args.experiment)
    spec = yaml.safe_load(spec_path.read_text())["experiment"]
    prom_url = os.environ.get("PROM_URL", "http://localhost:19090")
    testrun = os.environ.get("TESTRUN", "steady-120rps")
    am_url = os.environ.get("ALERTMANAGER_URL", "http://localhost:19093")

    print(f"experiment : {spec['name']}   fault {spec['litmus_fault']} "
          f"{spec['fault_duration_s']}s + recovery {spec['recovery_window_s']}s")
    print(f"hypothesis : v{spec['hypothesis']['version']}   floor "
          f"{spec['min_rps_floor']:.0f} rps   testrun {testrun}")
    print("injecting  : ", end="")

    t0 = None

    def on_tick(sample):
        nonlocal t0
        if t0 is None:
            t0 = sample.sampled_at
            print("armed")
        rps = sample.client.get("client_rps")
        avail = sample.client.get("client_availability")
        replicas = sample.server.get("payment_replicas_available")
        print(f"  t+{sample.sampled_at - t0:5.1f}s  rps={_f(rps, '.0f')}  "
              f"avail={_f(avail, '.4f')}  payment_replicas={_f(replicas, '.0f')}")

    try:
        r = runner.run(spec_path, prom_url, testrun, am_url, on_tick=on_tick,
                       require_override=getattr(args, "override", False))
    except PreflightSkip as e:
        # SKIPPED/DENIED are first-class recorded outcomes, never silent no-ops.
        print("REFUSED")
        execution_id = runner.record_refusal(spec, e.verdict, e.reason, e.evidence)
        print()
        print(f"execution  : #{execution_id}  (refusal recorded, nothing injected)")
        print(f"VERDICT    : {VERDICT_BANNER.get(e.verdict, e.verdict.upper())} — {e.reason}")
        return EXIT_CODES.get(e.verdict, 5)

    engine, verdict, execution_id = r["engine"], r["verdict"], r["execution_id"]
    facts, chaos, samples = r["facts"], r["chaos"], r["samples"]
    check_list, score = r["checks"], r["score"]

    print()
    radius = r["preflight"].radius
    if radius:
        print(f"blast      : score {radius.score()}  {radius.affected_pods} pod(s)  "
              f"{radius.requests_at_risk_per_min:.0f} req/min at risk  "
              f"budget burn {radius.error_budget_burn_pct:.3f}%")
    print(f"engine     : {engine}   litmus verdict={chaos.get('verdict')}")
    print(f"load       : achieved {facts.achieved_rps:.1f} rps   "
          f"dropped {facts.dropped_iterations:.0f}   coverage {facts.coverage:.0%}")
    print(f"execution  : #{execution_id}   samples {len(samples.samples)}")
    print()

    if verdict.outcomes:
        print(f"{'INVARIANT':32} {'KIND':16} {'OUTCOME':10} "
              f"{'WORST':>10} {'THRESHOLD':>10} {'BREACHED':>9}")
        for o in verdict.outcomes:
            kind = o.evidence.get("kind", "-")
            worst = "-" if o.worst_value is None else f"{o.worst_value:.4g}"
            print(f"{o.name:32} {kind:16} {o.outcome.upper():10} "
                  f"{worst:>10} {o.threshold:>10.4g} {o.breached_for_s:>8.1f}s")
        print()

    print(f"{'CHECK':24} {'APPLICABLE':11} {'OUTCOME':9} {'SCORE':>6}  DETAIL")
    for c in check_list:
        s = " n/a" if c.score is None else f"{c.score:.2f}"
        print(f"{c.check_type:24} {str(c.applicable):11} {c.outcome.upper():9} "
              f"{s:>6}  {c.actual_value or c.message}")
    print()
    if score.score is None:
        print(f"SCORE      : none — {score.reason}")
    else:
        excl = f", excluded {score.excluded}" if score.excluded else ""
        print(f"SCORE      : {score.score:.4f}  ({score.status}, "
              f"weights denominator {score.weights_denominator}{excl})")
    print()

    if r["aborted_by"]:
        w = r["watchdog"]
        print(f"ABORT      : {r['aborted_by']} — {w.reason}")
    cl = r["cleanup"]
    steps = "  ".join(f"{s.name}={'ok' if s.ok else 'FAILED'}" for s in cl.steps)
    print(f"cleanup    : {steps}")
    if cl.escalated:
        print(f"             ESCALATED — {cl.escalation_reason}")
    print()

    banner = VERDICT_BANNER.get(verdict.verdict, verdict.verdict.upper())
    print(f"VERDICT    : {banner}" + (f" — {verdict.reason}" if verdict.reason else ""))
    return EXIT_CODES.get(verdict.verdict, 6)


# --------------------------------------------------------------------------- #
# Phase 8 — the quality surface.
# --------------------------------------------------------------------------- #

def _connect():
    import psycopg

    from .db import DSN
    return psycopg.connect(DSN, connect_timeout=10)


def _all_experiment_names() -> list[str]:
    return sorted(yaml.safe_load(path.read_text())["experiment"]["name"]
                  for path in EXPERIMENTS_DIR.glob("*.yaml"))


def cmd_flakiness(args) -> int:
    """sigma, flip rate, and gating status per experiment.

    Read-only WITHOUT --apply. Measuring and acting are separate on purpose:
    quarantining an experiment opens a scoring epoch, and that is not something
    an operator should trigger by running a status command.
    """
    from .quality import flakiness as F
    from .quality import quarantine

    names = [args.experiment] if args.experiment else _all_experiment_names()
    if not names:
        print("no experiments recorded yet")
        return 0

    print(f"{'EXPERIMENT':32} {'STATUS':16} {'WINDOW':>7} {'SIGMA':>8} "
          f"{'FLIPS':>7}  AUTHORITY")
    transitions = []
    with _connect() as conn, conn.cursor() as cur:
        for name in names:
            verdict = F.evaluate(name, F.window_for(cur, name))
            sigma = "-" if verdict.sigma is None else f"{verdict.sigma:.4f}"
            flips = "-" if verdict.flip_rate is None else f"{verdict.flip_rate:.1%}"
            authority = "GATING" if verdict.gating else "advisory"
            print(f"{name:32} {verdict.status:16} {verdict.window_runs:>7} "
                  f"{sigma:>8} {flips:>7}  {authority}")
            if args.apply:
                transitions.append(quarantine.apply(
                    cur, name, verdict,
                    file_issue=not args.no_issue, notify=not args.no_slack))
        if args.apply:
            conn.commit()

    if not args.apply:
        print()
        print("read-only. Re-run with --apply to record these statuses; a change "
              "to the gating bit opens a new scoring epoch.")
        return 0

    print()
    for t in transitions:
        if t.gating_changed or t.errors:
            print(f"  {t.summary()}")
        for err in t.errors:
            print(f"    ! {err}")
    return 0


def cmd_epoch(args) -> int:
    from .quality import epochs

    with _connect() as conn, conn.cursor() as cur:
        if args.open:
            if not args.reason:
                raise SystemExit(
                    "--open requires --reason: an epoch boundary with no stated "
                    "reason is a break in the trend chart nobody can explain later")
            epoch = epochs.open_epoch(cur, args.reason)
            conn.commit()
            print(f"epoch {epoch.short()}  (id {epoch.id})  {epoch.change_reason}")
            return 0

        history = epochs.history(cur)
        live = epochs.current(cur)
        print(f"{'ID':>3} {'EPOCH':14} {'SCORER':8} {'SLO':>4} "
              f"{'GATING SET':30} REASON")
        for e in history:
            marker = "*" if e.sha256 == live.sha256 else " "
            gating = ", ".join(e.experiment_set) or "(none - all advisory)"
            print(f"{marker}{e.id:>2} {e.short():14} {e.scorer_version:8} "
                  f"{e.slo_version:>4} {gating[:30]:30} "
                  f"{(e.change_reason or '')[:70]}")
        print()
        print(f"* = the epoch the CURRENT code implies ({live.short()})")
        if not any(e.sha256 == live.sha256 for e in history):
            print("  WARNING: the current code implies an epoch that has never been "
                  "recorded. A scoring change was made without opening one.")
        return 0


def cmd_retro_score(args) -> int:
    """Re-score the last N runs under the current epoch, from stored samples."""
    from .quality import retro

    with _connect() as conn, conn.cursor() as cur:
        results, epoch = retro.rescore_last(
            cur, args.last, args.reason, experiment=args.experiment)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()

    print(f"epoch {epoch.short()}  {epoch.change_reason}")
    print()
    print(f"{'RUN':>6} {'EXPERIMENT':30} {'WAS':>8} {'NOW':>8}  METHOD / NOTE")
    changed = 0
    for r in results:
        if r.skipped_reason:
            print(f"{r.execution_id:>6} {r.experiment:30} "
                  f"{_score(r.original_score):>8} {'-':>8}  "
                  f"skipped: {r.skipped_reason}")
            continue
        changed += bool(r.changed)
        method = (f"re-derived {len(r.method.get('re_derived', []))}, "
                  f"carried {len(r.method.get('carried_over', []))}")
        print(f"{r.execution_id:>6} {r.experiment:30} "
              f"{_score(r.original_score):>8} {_score(r.new_score):>8}  {method}")

    print()
    print(f"{len(results)} run(s), {changed} score(s) moved.")
    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply to persist. "
              "Retro-scores are NEW ROWS under the new epoch; the original "
              "scores are never overwritten.")
    return 0


def _score(v) -> str:
    return "none" if v is None else f"{v:.4f}"


def _f(v, spec) -> str:
    return "-" if v is None else format(v, spec)


def main() -> int:
    parser = argparse.ArgumentParser(prog="chaosctl")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run one experiment, print per-invariant verdicts")
    p_run.add_argument("experiment")
    p_run.add_argument("--override", action="store_true",
                       help="satisfy a require_override policy rule (recorded)")
    p_run.set_defaults(func=cmd_run)
    p_list = sub.add_parser("list", help="list registered experiments")
    p_list.set_defaults(func=cmd_list)

    p_flake = sub.add_parser(
        "flakiness", help="sigma, flip rate and gating status per experiment")
    p_flake.add_argument("--experiment")
    p_flake.add_argument("--apply", action="store_true",
                         help="record the measured status; a change to the gating "
                              "bit opens a new scoring epoch")
    p_flake.add_argument("--no-issue", action="store_true",
                         help="skip filing a GitHub issue on quarantine")
    p_flake.add_argument("--no-slack", action="store_true",
                         help="skip the Slack notice on quarantine")
    p_flake.set_defaults(func=cmd_flakiness)

    p_epoch = sub.add_parser("epoch", help="inspect or open a scoring epoch")
    p_epoch.add_argument("--show", action="store_true", default=True)
    p_epoch.add_argument("--open", action="store_true")
    p_epoch.add_argument("--reason")
    p_epoch.set_defaults(func=cmd_epoch)

    p_retro = sub.add_parser(
        "retro-score", help="re-score the last N runs under the current epoch")
    p_retro.add_argument("--last", type=int, default=20)
    p_retro.add_argument("--experiment")
    p_retro.add_argument("--reason", required=True)
    p_retro.add_argument("--apply", action="store_true",
                         help="persist. Without it this is a dry run.")
    p_retro.set_defaults(func=cmd_retro_score)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
