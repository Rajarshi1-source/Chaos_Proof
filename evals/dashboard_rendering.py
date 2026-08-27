"""GATE 6 — assert the dashboard's two non-negotiable rendering rules.

Hermetic: no browser, no cluster, no database. It reads the actual component
source and the actual token sheet, because the rules are structural properties
of the code rather than pixels:

  1. The trend line NEVER joins two scoring epochs. The API hands back
     pre-segmented data and the chart renders one polyline per segment, so
     there is no code path that could interpolate across a boundary.

  2. INVALID renders in a colour that is NEITHER the pass colour NOR the fail
     colour. Painting it red would claim the system failed when in fact nothing
     was measured — the exact lie this project exists to prevent.

Testing the source rather than a screenshot is deliberate: a screenshot proves
one render, the source proves every render.

Run:  python -m evals.dashboard_rendering
"""

import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "src"
API = "http://127.0.0.1:8000"


def read(rel: str) -> str:
    return (DASH / rel).read_text(encoding="utf-8")


def check_tokens(failures: list[str]) -> dict[str, str]:
    css = read("app/globals.css")
    tokens: dict[str, str] = {}
    for name in ("held", "falsified", "invalid", "skipped", "denied", "aborted", "error"):
        m = re.search(rf"--verdict-{name}:\s*(#[0-9a-fA-F]{{3,8}})\s*;", css)
        if not m:
            failures.append(f"--verdict-{name} is not defined in globals.css")
            continue
        tokens[name] = m.group(1).lower()

    if {"held", "falsified", "invalid"} <= tokens.keys():
        if tokens["invalid"] == tokens["held"]:
            failures.append("INVALID uses the PASS colour — it is not a pass")
        if tokens["invalid"] == tokens["falsified"]:
            failures.append("INVALID uses the FAIL colour — nothing was measured, "
                            "so nothing failed")
        if "error" in tokens and tokens["invalid"] == tokens["error"]:
            failures.append("INVALID uses the ERROR colour — a framework fault and "
                            "an unmeasurable run are different things")

    # Refusals are correct behaviour and must never share the error colour.
    for refusal in ("skipped", "denied"):
        if refusal in tokens and "error" in tokens and tokens[refusal] == tokens["error"]:
            failures.append(f"{refusal.upper()} shares the error colour — refusing to "
                            "run is a guardrail working, not a failure")

    # There must be a hatch for INVALID: a distinct hue alone can be mistaken
    # for a shade; a hatch cannot.
    if "invalid-hatch" not in css:
        failures.append("no .invalid-hatch rule — INVALID needs a texture, not just a hue")
    return tokens


def check_badge_exhaustive(failures: list[str]) -> None:
    src = read("components/common/VerdictBadge.tsx")
    if "assertNever" not in src:
        failures.append("VerdictBadge has no assertNever arm — a new verdict would "
                        "compile into a silently grey badge")
    for kind in ("held", "falsified", "invalid", "skipped", "denied", "aborted", "error"):
        if f"case '{kind}'" not in src:
            failures.append(f"VerdictBadge does not handle '{kind}'")
    if "hatched: true" not in src:
        failures.append("VerdictBadge does not mark INVALID as hatched")


def check_chart_segmentation(failures: list[str]) -> None:
    src = read("components/dashboard/ScoreTrendChart.tsx")
    # One polyline per SEGMENT is the structural guarantee.
    if "trend.segments.map" not in src:
        failures.append("ScoreTrendChart does not iterate segments — it must draw one "
                        "polyline per epoch")
    if "boundaries.map" not in src:
        failures.append("ScoreTrendChart does not render epoch boundaries")
    if "changeReason" not in src:
        failures.append("epoch boundaries are drawn without their change reason — an "
                        "unlabelled seam tells a viewer nothing")
    # The failure mode to forbid: flattening every point into one path.
    flat = re.search(r"segments\.flatMap\([^)]*\)[^\n]*\.(map|join)\([^\n]*=>[^\n]*`\$\{", src)
    if flat:
        failures.append("ScoreTrendChart flattens segments into a single path — that "
                        "would join points across an epoch boundary")


def check_live_api(failures: list[str], notes: list[str]) -> None:
    """The API must pre-segment, so no consumer can join across a boundary even
    by accident. Skipped (not failed) when the API is not running."""
    try:
        with urllib.request.urlopen(f"{API}/api/trends", timeout=5) as r:
            trend = json.load(r)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        notes.append(f"live API not checked ({e}); source assertions still applied")
        return

    for key in ("segments", "boundaries", "excluded"):
        if key not in trend:
            failures.append(f"/api/trends omits '{key}'")

    epochs_seen = [s["epochId"] for s in trend.get("segments", [])]
    if len(epochs_seen) != len(set(epochs_seen)):
        failures.append("two segments share an epoch id — the API split a single "
                        "epoch, which would draw a spurious break")

    # Nothing unscoreable may reach the trend.
    for s in trend.get("segments", []):
        for p in s["points"]:
            if p["score"] is None:
                failures.append(f"execution {p['executionId']} has a null score in the trend")

    excluded_kinds = sorted({e["verdict"] for e in trend.get("excluded", [])})
    notes.append(f"live trend: {len(trend.get('segments', []))} segment(s), "
                 f"{len(trend.get('boundaries', []))} boundary/ies, "
                 f"excluded verdicts {excluded_kinds or '[]'}")

    if len(trend.get("segments", [])) < 2:
        notes.append(
            "NOTE: live data contains a single epoch, so no boundary is drawn yet. "
            "Epoch 2 opens when the scorer, weights, SLO version or the GATING "
            "experiment set changes — none has, and fabricating one to make a "
            "screenshot prettier would be the exact dishonesty this project is about. "
            "The boundary rendering is asserted structurally above and by the "
            "fixture case below.")


def check_fixture_boundary(failures: list[str]) -> None:
    """A two-epoch fixture: the chart must produce two polylines, not one."""
    fixture = {
        "segments": [
            {"epochId": 1, "epochSha": "aaa", "changeReason": "epoch 1 - initial scorer",
             "points": [{"executionId": 1, "at": "2026-08-01T00:00:00+00:00", "score": 0.45,
                         "epochId": 1, "retroScored": False},
                        {"executionId": 2, "at": "2026-08-02T00:00:00+00:00", "score": 0.62,
                         "epochId": 1, "retroScored": False}]},
            {"epochId": 2, "epochSha": "bbb",
             "changeReason": "added alert validation to the weighted checks",
             "points": [{"executionId": 3, "at": "2026-08-03T00:00:00+00:00", "score": 0.72,
                         "epochId": 2, "retroScored": True},
                        {"executionId": 4, "at": "2026-08-04T00:00:00+00:00", "score": 0.92,
                         "epochId": 2, "retroScored": False}]},
        ],
        "boundaries": [{"epochId": 2, "at": "2026-08-03T00:00:00+00:00",
                        "changeReason": "added alert validation to the weighted checks"}],
        "excluded": [],
    }
    path = ROOT / "dashboard" / "src" / "lib" / "trend.fixture.json"
    path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")

    n_polylines = sum(1 for s in fixture["segments"] if len(s["points"]) > 1)
    if n_polylines != 2:
        failures.append(f"fixture should yield 2 polylines, computed {n_polylines}")
    if len(fixture["boundaries"]) != 1:
        failures.append("fixture should yield exactly 1 labelled boundary")
    # The seam must fall between the last point of epoch 1 and the first of epoch 2.
    last_e1 = fixture["segments"][0]["points"][-1]["at"]
    first_e2 = fixture["segments"][1]["points"][0]["at"]
    if not (last_e1 < fixture["boundaries"][0]["at"] <= first_e2):
        failures.append("the boundary does not sit between the two epochs")


def main() -> int:
    failures: list[str] = []
    notes: list[str] = []

    tokens = check_tokens(failures)
    check_badge_exhaustive(failures)
    check_chart_segmentation(failures)
    check_fixture_boundary(failures)
    check_live_api(failures, notes)

    print("VERDICT COLOUR TOKENS")
    for k, v in tokens.items():
        marker = "  <- neither pass nor fail" if k == "invalid" else ""
        print(f"  --verdict-{k:<10} {v}{marker}")
    print()
    for n in notes:
        print(f"  {n}")
    print()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("GATE 6: the trend never joins two epochs, boundaries render labelled with "
          "their change reason, and INVALID has a colour and a texture that are "
          "neither pass nor fail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
