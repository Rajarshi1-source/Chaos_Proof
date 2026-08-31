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
    python -m src.chaosctl contracts generate [--service X] [--write]
    python -m src.chaosctl contracts validate <service> [--run]
    python -m src.chaosctl counterfactual run <experiment> [-n 5]
    python -m src.chaosctl counterfactual report [--metric failed_requests]
    python -m src.chaosctl replay <sha>            # OFFLINE: no cluster, no network, no DB
    python -m src.chaosctl audit --verify-bundles
    python -m src.chaosctl postmortem <sha>
Env:
    PROM_URL (default http://localhost:19090), TESTRUN (default steady-120rps)
    CHAOSPROOF_DB, CHAOSPROOF_SLACK_WEBHOOK, GITHUB_TOKEN
"""

import argparse
import os
import pathlib
import sys
import tempfile

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
    for path in sorted(EXPERIMENTS_DIR.rglob("*.yaml")):
        spec = yaml.safe_load(path.read_text())["experiment"]
        if spec["name"] == name:
            return path
    raise SystemExit(f"no experiment named {name!r} in {EXPERIMENTS_DIR}. "
                     f"Try: python -m src.chaosctl list")


def cmd_list(_args) -> int:
    print(f"{'NAME':32} {'FAULT':22} {'FLOOR':>6}  HYPOTHESIS")
    for path in sorted(EXPERIMENTS_DIR.rglob("*.yaml")):
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
                  for path in EXPERIMENTS_DIR.rglob("*.yaml"))


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
                transitions.append(quarantine.apply(cur, name, verdict))
        if args.apply:
            # COMMIT BEFORE ANNOUNCING. An issue filed for a demotion that then
            # rolled back leaves GitHub and the evidence store disagreeing —
            # and the issue is the more visible of the two.
            conn.commit()

    if args.apply:
        with _connect() as conn, conn.cursor() as cur:
            for t in transitions:
                quarantine.announce(t, file_issue=not args.no_issue,
                                    notify=not args.no_slack)
                quarantine.link_issue(cur, t)
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


# --------------------------------------------------------------------------- #
# Phase 11 — offline replay, bundle audit, postmortem.
# --------------------------------------------------------------------------- #

def cmd_replay(args) -> int:
    """`chaosctl replay <sha>` — OFFLINE reproduction.

    No cluster, no network, no database. The bundle on disk is the only input.
    This function deliberately imports nothing that opens a socket, and the
    output format is stable because it doubles as a golden-file test and the
    dashboard renders it for the public read-only demo.
    """
    from .quality import bundle_store, replay

    try:
        sha = bundle_store.resolve(args.sha)
        bundle = bundle_store.read(sha)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 2
    except bundle_store.BundleImmutabilityError as exc:
        print(f"REFUSING TO REPLAY: {exc}")
        return 3

    print(replay.render(bundle))
    return 0


def cmd_audit(args) -> int:
    """Re-hash every stored bundle; exit non-zero on any mismatch."""
    from .quality import bundle_store

    checked, problems = bundle_store.verify_all()
    if not checked:
        print("no bundles stored yet")
        return 0
    for problem in problems:
        print(f"  MISMATCH {problem}")
    if problems:
        print(f"\nAUDIT: {len(problems)} of {checked} bundles failed verification. "
              "A bundle that no longer hashes to its own contents has been "
              "modified, and its replay output describes a run that did not happen.")
        return 1
    print(f"AUDIT: {checked} bundle(s), all hash to their own contents.")
    return 0


def cmd_postmortem(args) -> int:
    from .postmortem import narrator as N
    from .quality import bundle_store

    try:
        bundle = bundle_store.read(bundle_store.resolve(args.sha))
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 2

    writer = N.default()
    draft = writer.draft(bundle)
    print(draft.render())
    if draft.discarded:
        print()
        print(f"NOTE: an LLM draft was discarded — {draft.discard_reason}")
    return 0


# --------------------------------------------------------------------------- #
# Phase 10 — counterfactual pairs.
# --------------------------------------------------------------------------- #

def cmd_counterfactual(args) -> int:
    from .counterfactual import analysis as A
    from .counterfactual import cost as C
    from .counterfactual import runner as CF

    if args.counterfactual_command == "report":
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT pattern_name FROM counterfactual_runs "
                        "ORDER BY pattern_name")
            patterns = [r[0] for r in cur.fetchall()]
            if not patterns:
                print("no counterfactual pairs recorded yet")
                return 0
            lower = args.metric in ("failed_requests", "p99_ms", "recovery_s")
            for pattern in patterns:
                pair = CF.load_pair(cur, pattern)
                if pair is None:
                    continue
                result = pair.analyse(args.metric, lower_is_better=lower)
                print(C.render(result, C.estimate(result)))
                if pair.discarded():
                    print(f"  discarded {len(pair.discarded())} unusable run(s): "
                          + ", ".join(f"{r.arm}#{r.repetition}={r.verdict}"
                                      for r in pair.discarded()))
                print()
        return 0

    # run
    spec_path = _resolve(args.experiment)
    spec = yaml.safe_load(spec_path.read_text())["experiment"]

    # Refuses BEFORE anything is disabled.
    plan = CF.plan_pair(spec, repetitions=args.repetitions)

    prom_url = os.environ.get("PROM_URL", "http://localhost:19090")
    testrun = os.environ.get("TESTRUN", "staging-120rps")
    am_url = os.environ.get("ALERTMANAGER_URL", "http://localhost:19093")

    pair_id = CF.new_pair_id()
    pattern = spec["disable_patterns"][0]
    ns = spec["target"]["namespace"]
    flag_app = spec.get("flag_target", "order-api")

    print(f"counterfactual pair {pair_id}")
    print(f"  experiment {spec['name']}   pattern {pattern}")
    print(f"  {args.repetitions} repetitions per arm, INTERLEAVED "
          f"(with, without, with, ...) so cluster drift affects both arms equally")
    print(f"  namespace {ns} (staging only, by policy)")
    print()

    runs: list[CF.ArmRun] = []
    for index, (arm, repetition) in enumerate(plan, start=1):
        arm_spec = CF.spec_for_arm(spec, arm)
        label = f"[{index}/{len(plan)}] {arm} #{repetition}"
        print(f"{label} ...", flush=True)

        # OUTSIDE experiments/, deliberately. Writing per-arm specs beside the
        # real ones let a leftover temp file be RESOLVED AS AN EXPERIMENT: a
        # killed run left `.name.with_pattern.tmp.yaml` behind, and because the
        # with-arm spec has `disable_patterns` stripped, the next pair resolved
        # that file and refused itself with "no disable_patterns". The safety
        # check was right; the input was the wrong file. A scratch directory
        # cannot collide with the experiment registry at all.
        tmp_dir = pathlib.Path(tempfile.gettempdir()) / "chaosproof-arms"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{spec['name']}.{arm}.yaml"
        tmp.write_text(yaml.safe_dump({"experiment": arm_spec}, sort_keys=False),
                       encoding="utf-8")
        try:
            r = runner.run(tmp, prom_url, testrun, am_url, require_override=True)
            failed, p99 = CF.metrics_from_samples(r["samples"])
            run = CF.ArmRun(arm, repetition, r["execution_id"],
                            r["verdict"].verdict, failed, p99)
            print(f"       execution #{run.execution_id}  {run.verdict}  "
                  f"failed~{0 if failed is None else failed:.0f}  "
                  f"p99 {0 if p99 is None else p99:.0f}ms"
                  + ("" if run.usable else "   (not usable - excluded)"))
        except PreflightSkip as e:
            execution_id = runner.record_refusal(spec, e.verdict, e.reason, e.evidence)
            run = CF.ArmRun(arm, repetition, execution_id, e.verdict, error=e.reason)
            print(f"       REFUSED {e.verdict}: {e.reason}")
        except Exception as exc:                                   # noqa: BLE001
            run = CF.ArmRun(arm, repetition, None, "error", error=str(exc))
            print(f"       ERROR {type(exc).__name__}: {exc}")
        finally:
            tmp.unlink(missing_ok=True)

        runs.append(run)
        with _connect() as conn, conn.cursor() as cur:
            CF.record(cur, pair_id, pattern, run)
            conn.commit()

        # Between arms: verify the flags actually came back before starting the
        # next one. A pair that half-restores leaves the next arm measuring a
        # mixture of two configurations.
        try:
            CF.verify_flags_restored(ns, flag_app, {})
        except CF.CounterfactualSafetyError as exc:
            print(f"       HALTING PAIR: {exc}")
            break
        except Exception as exc:                                   # noqa: BLE001
            print(f"       note: could not verify flags ({type(exc).__name__})")

        if index < len(plan):
            CF.wait_between_arms(args.settle_s)

    pair = CF.PairResult(pair_id, pattern, spec["name"], runs)
    result = pair.analyse("failed_requests")
    print()
    print(C.render(result, C.estimate(result)))
    return 0


# --------------------------------------------------------------------------- #
# Phase 9 — resilience contracts.
# --------------------------------------------------------------------------- #

def cmd_contracts(args) -> int:
    from .contracts import generator, model, validate

    if args.contracts_command == "generate":
        contracts = ([model.for_service(args.service)] if args.service
                     else model.load_all())
        for contract in contracts:
            gen = generator.generate(contract)
            tested, total, ratio = generator.coverage(gen)
            print(f"{contract.service} v{contract.version}  "
                  f"{tested}/{total} clauses falsifiable ({ratio:.0%})")
            for g in gen:
                mark = " " if g.testable else "-"
                note = "" if g.testable else f"   UNTESTABLE: {g.untested_reason}"
                kind = ("observation" if g.spec.get("observation_only")
                        else g.spec.get("litmus_fault", ""))
                print(f"  {mark} {g.provenance.clause:52} {kind:22}{note}")
            if args.write:
                written = validate.write_generated(contract, gen)
                print(f"    wrote {len(written)} experiment file(s) to "
                      f"experiments/generated/")
            print(f"    {generator.gating_note()}")
            print()
        return 0

    # validate
    contract = model.for_service(args.service)
    gen = generator.generate(contract)
    outcomes = {}
    try:
        with _connect() as conn, conn.cursor() as cur:
            if args.backfill:
                done = validate.backfill(cur, contract, gen)
                conn.commit()
                print(f"backfilled {len(done)} clause outcome(s) from stored runs")
                print()
            outcomes = validate.load_recorded(cur, contract)
    except Exception as exc:                                       # noqa: BLE001
        print(f"note: evidence store unreachable ({type(exc).__name__}); "
              "reporting declared clauses with no recorded outcomes")

    if args.observe:
        # does_not_inflict clauses are about LOAD, not faults: nothing is
        # injected, they are measured against live Prometheus.
        from .integrations.prometheus import PrometheusClient
        prom = PrometheusClient(os.environ.get("PROM_URL", "http://localhost:19090"))
        outcomes.update(validate.observe_inflict_clauses(
            prom, contract, generator.generate(contract)))

    rep = validate.report_for(contract, outcomes, epoch=validate.current_epoch_sha())
    print(rep.render())
    if args.metrics:
        print()
        print(rep.prometheus_metrics())
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

    p_con = sub.add_parser("contracts", help="resilience contracts (§19)")
    con_sub = p_con.add_subparsers(dest="contracts_command", required=True)

    p_gen = con_sub.add_parser(
        "generate", help="emit one experiment per tolerates clause")
    p_gen.add_argument("--service")
    p_gen.add_argument("--write", action="store_true",
                       help="materialise experiments into experiments/generated/")
    p_gen.set_defaults(func=cmd_contracts)

    p_val = con_sub.add_parser(
        "validate", help="report HONOURED / VIOLATED / UNTESTED per clause")
    p_val.add_argument("service")
    p_val.add_argument("--metrics", action="store_true",
                       help="also print the Prometheus exposition text")
    p_val.add_argument("--backfill", action="store_true",
                       help="re-derive clause outcomes from stored sli_samples "
                            "for generated experiments that already ran")
    p_val.add_argument("--observe", action="store_true",
                       help="measure does_not_inflict clauses against live "
                            "Prometheus (needs PROM_URL)")
    p_val.set_defaults(func=cmd_contracts)

    p_cf = sub.add_parser("counterfactual",
                          help="pattern on-vs-off pairs (§17, staging only)")
    cf_sub = p_cf.add_subparsers(dest="counterfactual_command", required=True)

    p_cf_run = cf_sub.add_parser("run", help="run one interleaved pair")
    p_cf_run.add_argument("experiment")
    p_cf_run.add_argument("-n", "--repetitions", type=int, default=5,
                          help="repetitions PER ARM (minimum 5; below that the "
                               "analysis refuses to report a delta)")
    p_cf_run.add_argument("--settle-s", type=float, default=30.0,
                          help="settle time between arms")
    p_cf_run.set_defaults(func=cmd_counterfactual)

    p_cf_rep = cf_sub.add_parser("report", help="analyse recorded pairs")
    p_cf_rep.add_argument("--metric", default="failed_requests")
    p_cf_rep.set_defaults(func=cmd_counterfactual)

    p_replay = sub.add_parser(
        "replay", help="offline reproduction from a stored evidence bundle")
    p_replay.add_argument("sha", help="bundle sha256, or a unique prefix")
    p_replay.set_defaults(func=cmd_replay)

    p_audit = sub.add_parser("audit", help="re-hash stored evidence bundles")
    p_audit.add_argument("--verify-bundles", action="store_true", default=True)
    p_audit.set_defaults(func=cmd_audit)

    p_pm = sub.add_parser("postmortem", help="draft a postmortem from a bundle")
    p_pm.add_argument("sha")
    p_pm.set_defaults(func=cmd_postmortem)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
