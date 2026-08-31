"""`chaosctl replay <sha>` (§22.1) — offline reproduction.

No cluster, no network, no database. The bundle on disk is the only input, and
that constraint is the point: a resilience score is a claim about production
behaviour, and anyone can put a number on a dashboard. This one comes with a
signed evidence bundle you can replay on a laptop with the wifi off and get
byte-identical output. That is the difference between a metric and an
assertion.

THE OUTPUT FORMAT IS AN API. It doubles as a golden-file test and the dashboard
renders it for the public read-only demo, so changing a column width breaks
both. Three rules keep it stable:

  1. NOTHING NON-DETERMINISTIC. No wall-clock, no locale, no dict iteration
     order, no floating-point formatting that varies by platform. Every
     timestamp printed is relative to the run's own start, taken from the
     bundle.
  2. THE VERDICT IS READ, NOT RECOMPUTED. Replay renders what the run
     concluded. Re-deriving it here would make the output depend on the
     CURRENT scorer, so replaying an old bundle after a scoring change would
     silently print a number that run never produced. Re-deriving under a new
     epoch is retro-scoring, and it is a different, explicitly-labelled
     operation.
  3. EVERY NUMBER COMES FROM THE BUNDLE. The same rule the narrator obeys.
"""

from . import bundles

WIDTH = 78


def _f(value, spec: str, fallback: str = "-") -> str:
    if value is None:
        return fallback
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return str(value)


def _short(sha: str | None, n: int = 12) -> str:
    return (sha or "")[:n] or "-"


def render(bundle: dict) -> str:
    """The stable, deterministic rendering of one evidence bundle."""
    lines: list[str] = []
    load = bundle.get("load") or {}
    hypothesis = bundle.get("hypothesis") or {}
    epoch = bundle.get("epoch") or {}
    score = bundle.get("score") or {}

    # --- identity ---------------------------------------------------------- #
    lines.append(f"experiment : {bundle.get('experiment', '-'):<28} "
                 f"epoch {_short(epoch.get('epoch_sha256'))}     "
                 f"git {_short(bundle.get('git_sha'), 7)}")

    # --- the load plane, first, because a run with no load has no evidence -- #
    achieved = load.get("achieved_rps")
    floor = load.get("min_rps_floor")
    dropped = load.get("dropped_iterations")
    coverage = load.get("coverage")
    valid = (achieved is not None and floor is not None
             and float(achieved) >= float(floor)
             and float(dropped or 0) <= 10
             and float(coverage or 0) >= 0.90)
    lines.append(
        f"load       : {load.get('tool', 'k6')} {load.get('tool_version', '-')}  "
        f"{load.get('workload_model', 'open')} model  "
        f"target {_f(load.get('target_rps'), '.0f')} rps  "
        f"achieved {_f(achieved, '.1f')}  "
        f"dropped {_f(dropped, '.0f')}    "
        f"{'VALID' if valid else 'INVALID'}")

    version = hypothesis.get("version", "-")
    description = " ".join((hypothesis.get("description") or "").split())
    if len(description) > 52:
        description = description[:49] + "..."
    lines.append(f'hypothesis : v{version}  "{description}"')

    # --- per-invariant outcomes -------------------------------------------- #
    for outcome in bundle.get("invariant_outcomes") or []:
        name = outcome.get("name", "-")
        worst = outcome.get("worst_value")
        threshold = outcome.get("threshold")
        comparator = outcome.get("comparator", "")
        breached = outcome.get("breached_for_s")
        lines.append(
            f"  {name:<27} worst {_f(worst, '.4g'):>8}  "
            f"{comparator:>2} {_f(threshold, '.4g'):<8}  "
            f"breached {_f(breached, '.0f')}s{'':<4} "
            f"{str(outcome.get('outcome', '')).upper()}")

    # --- verdict and score, READ from the bundle ---------------------------- #
    verdict = str(bundle.get("verdict", "")).upper()
    if score.get("score") is None:
        lines.append(f"verdict    : {verdict:<22} score none  "
                     f"({score.get('reason') or 'not scoreable'})")
    else:
        lines.append(f"verdict    : {verdict:<22} score {_f(score.get('score'), '.4f')}  "
                     f"(weights denom {_f(score.get('weights_denominator'), '.2f')})")

    # --- checks ------------------------------------------------------------- #
    for check in bundle.get("checks") or []:
        applicable = "n/a " if not check.get("applicable") else (
            _f(check.get("score"), '.2f') + " ")
        lines.append(f"  {check.get('check_type', '-'):<27} "
                     f"{str(check.get('outcome', '')).upper():<9} {applicable:>5} "
                     f"{' '.join((check.get('message') or '').split())[:28]}")

    # --- safety ------------------------------------------------------------- #
    blast = bundle.get("blast_radius") or {}
    preflight = bundle.get("preflight") or {}
    abort = bundle.get("abort")
    lines.append(
        f"safety     : blast score {_f(blast.get('score'), '.0f')}  "
        f"budget burn {_f(blast.get('error_budget_burn_pct'), '.3f')}%  "
        f"gate {preflight.get('decision', '-')}  "
        + (f"ABORTED by {abort.get('path')} ({abort.get('condition')})"
           if abort else "no aborts"))

    cleanup = bundle.get("cleanup") or []
    if cleanup:
        steps = "  ".join(
            f"{s.get('name')}={'ok' if s.get('ok') else 'FAILED'}" for s in cleanup)
        lines.append(f"cleanup    : {steps}")

    # --- the bundle's own identity, last ------------------------------------ #
    lines.append(f"bundle     : {bundle.get('bundle_sha256', '-')}")
    return "\n".join(lines)


def render_deterministic_check(bundle: dict) -> tuple[str, bool]:
    """Render, then confirm the bundle still hashes to its stored digest.

    GATE 11 asks for byte-identical output offline. Rendering is pure, so the
    only way the output could drift is if the INPUT drifted — which is exactly
    what the digest detects.
    """
    text = render(bundle)
    return text, bundles.verify(bundle)
