"""Alert-rule lint (§16.5, ruling D-J) — the impossible-SLO check.

An alert cannot fire faster than the pipeline that produces it. Its irreducible
detection latency is:

    scrape_interval  +  group evaluation_interval  +  for:

A rule whose irreducible floor exceeds its own alert-latency SLO can NEVER meet
that SLO. No amount of tuning the query helps, and grading a system against it
is grading an impossibility — the resulting "alerting is slow" finding is
really a statement about the Prometheus configuration.

This is genuinely useful tooling rather than box-ticking: it catches alert SLOs
that are arithmetically unreachable BEFORE anyone relies on them. `for: 1m` on a
30s group with 5s scraping has a 95s floor; against the 60s SLO this project
publishes, that rule is unreachable by construction.

Hermetic: reads the chart templates, does arithmetic, exits non-zero. No cluster.

Run:  python -m evals.alert_rule_lint
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "chaos-framework"))

from src.constants import TARGET_SCRAPE_INTERVAL_S                # noqa: E402
from src.validators.checks import SLO_ALERT_LATENCY_S             # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RULE_FILES = [REPO_ROOT / "charts" / "target-app" / "templates" / "prometheusrule.yaml"]
SERVICEMONITOR = REPO_ROOT / "charts" / "target-app" / "templates" / "servicemonitor.yaml"

DURATION = re.compile(r"^(\d+)(ms|s|m|h)$")
UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}


def seconds(value: str) -> float | None:
    m = DURATION.match(value.strip().strip("\"'"))
    if not m:
        return None
    return float(m.group(1)) * UNITS[m.group(2)]


def scrape_interval_s() -> float:
    """Read the ACTUAL scrape interval from the ServiceMonitor rather than
    trusting the constant. The arithmetic is only meaningful against the
    interval Prometheus is really using, and these two drifting apart is
    exactly the kind of thing this lint exists to catch."""
    if not SERVICEMONITOR.exists():
        return float(TARGET_SCRAPE_INTERVAL_S)
    for line in SERVICEMONITOR.read_text(encoding="utf-8").splitlines():
        if "interval:" in line and "scrapeTimeout" not in line:
            got = seconds(line.split("interval:", 1)[1].split("#")[0])
            if got:
                return got
    return float(TARGET_SCRAPE_INTERVAL_S)


def parse_rules(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Deliberately a line scanner, not a YAML parse.

    These are Helm templates: they contain `{{ ... }}` expressions that are not
    valid YAML until rendered, and requiring a rendered chart would mean this
    lint needed Helm — which would push it out of the hermetic CI stage where
    it belongs and into the cluster job where it is 14 minutes too late.
    """
    rules: list[dict] = []
    problems: list[str] = []
    group_interval: float | None = None
    current: dict | None = None

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#")[0].rstrip()
        stripped = line.strip()

        if stripped.startswith("interval:") and current is None:
            group_interval = seconds(stripped.split("interval:", 1)[1])
            continue

        if stripped.startswith("- alert:"):
            if current is not None:
                rules.append(current)
            current = {"alert": stripped.split("- alert:", 1)[1].strip(),
                       "line": lineno, "for": None,
                       "group_interval": group_interval}
            continue

        if current is not None and stripped.startswith("for:"):
            value = stripped.split("for:", 1)[1].strip()
            current["for"] = seconds(value)
            if current["for"] is None:
                problems.append(f"{path.name}:{lineno} {current['alert']}: "
                                f"unparseable `for: {value}`")

    if current is not None:
        rules.append(current)
    return rules, problems


def main() -> int:
    scrape = scrape_interval_s()
    failures: list[str] = []
    rows: list[tuple] = []

    for path in RULE_FILES:
        if not path.exists():
            failures.append(f"{path} not found")
            continue
        rules, problems = parse_rules(path)
        failures.extend(problems)
        if not rules:
            failures.append(f"{path.name}: no alert rules found — the lint would "
                            "pass vacuously, which is worse than failing")

        for rule in rules:
            name = rule["alert"]
            if rule["for"] is None:
                failures.append(f"{name}: no `for:` — an alert with no `for` fires "
                                "on a single scrape and will flap")
                continue
            if rule["group_interval"] is None:
                failures.append(f"{name}: group has no `interval:`, so its "
                                "evaluation cadence is Prometheus's default rather "
                                "than something this arithmetic can verify")
                continue

            irreducible = scrape + rule["group_interval"] + rule["for"]
            ok = irreducible <= SLO_ALERT_LATENCY_S
            rows.append((name, scrape, rule["group_interval"], rule["for"],
                         irreducible, ok))
            if not ok:
                failures.append(
                    f"{name}: IMPOSSIBLE SLO — irreducible latency "
                    f"{irreducible:.0f}s = scrape {scrape:.0f}s + evaluation "
                    f"{rule['group_interval']:.0f}s + for {rule['for']:.0f}s, "
                    f"which exceeds the {SLO_ALERT_LATENCY_S:.0f}s alert-latency "
                    "SLO it is graded against. This rule can never pass. Lower "
                    "`for:`, lower the group interval, or raise the SLO — but do "
                    "not grade a system against an unreachable target.")

    print(f"{'ALERT':32} {'SCRAPE':>7} {'EVAL':>6} {'FOR':>6} {'FLOOR':>7} "
          f"{'SLO':>5}  VERDICT")
    for name, sc, ev, fo, irr, ok in rows:
        print(f"{name:32} {sc:>6.0f}s {ev:>5.0f}s {fo:>5.0f}s {irr:>6.0f}s "
              f"{SLO_ALERT_LATENCY_S:>4.0f}s  {'ok' if ok else 'IMPOSSIBLE'}")

    print()
    if failures:
        print(f"ALERT RULE LINT: FAILED — {len(failures)} problem(s)")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    headroom = SLO_ALERT_LATENCY_S - max((r[4] for r in rows), default=0)
    print(f"ALERT RULE LINT: {len(rows)} rules, every irreducible latency within "
          f"the {SLO_ALERT_LATENCY_S:.0f}s SLO (tightest headroom {headroom:.0f}s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
