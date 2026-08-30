"""The replay eval (§21.2) — a CI gate, not a unit test.

Replays committed evidence bundles through the hypothesis engine and the
scorer. Hermetic: no cluster, no Prometheus, no database, no network. Runs on
every PR in seconds.

Two things are asserted, and the second is the one people skip:

  1. ACCURACY. Every case's verdict and score must match its label exactly.
     TARGET_ACCURACY is 1.00 — these decisions are deterministic, so any
     mismatch is a bug rather than a tolerance to widen.

  2. DETERMINISM. The digest of every (verdict, score) pair is compared against
     a frozen artefact keyed by SCORER_VERSION. When a deliberate logic change
     moves the digests, the reviewer updates the frozen set IN THE SAME PR —
     which makes every scoring-affecting change visible in review. That is the
     point of the mechanism, not an inconvenience to route around.

Run:  python -m evals.replay_eval
      python -m evals.replay_eval --update-digests   (deliberate change only)
"""

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "chaos-framework"))

from src.hypothesis.engine import evaluate                       # noqa: E402
from src.quality import bundles                                  # noqa: E402
from src.quality.epochs import EpochMismatch, assert_same        # noqa: E402
from src.scoring import scorer                                   # noqa: E402

CORPUS_DIR = pathlib.Path(__file__).resolve().parent / "corpus"
DIGEST_PATH = pathlib.Path(__file__).resolve().parent / "frozen_digests.json"

TARGET_ACCURACY = 1.00
MIN_CORPUS_SIZE = 20
SCORE_TOLERANCE = 1e-9

# Every defect that has been fixed must keep a case. A corpus that can lose one
# silently is a corpus that lets a regression back in.
REQUIRED_CASES = {
    "no_load_zero_traffic", "pattern_not_applicable", "empty_promql_series",
    "alert_for_exceeds_slo", "dropped_iterations_high", "abort_mid_experiment",
    "epoch_mismatch",
}

# Verdicts that never reach the engine: the run was refused or cut short before
# a hypothesis could be tested, so the bundle's recorded verdict IS the answer.
NON_EVALUATED = {"skipped", "denied", "aborted"}


def load_corpus() -> list[dict]:
    cases = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(CORPUS_DIR.glob("*.json"))]
    return sorted(cases, key=lambda c: c["name"])


def replay(case: dict) -> tuple[str, object, list[str]]:
    """Re-derive (verdict, score) from the bundle alone. Returns any failures."""
    bundle, expected, failures = case["bundle"], case["expected"], []
    name = case["name"]

    # The bundle must still hash to its own digest. A corpus file edited by hand
    # without regenerating is a labelled dataset that no longer matches its label.
    if not bundles.verify(bundle):
        failures.append(f"{name}: bundle digest does not match its contents")

    # Epoch check FIRST, and it is a refusal rather than a coercion.
    current = scorer.epoch_sha256([])
    if expected.get("refuses") == "EpochMismatch":
        try:
            assert_same(bundle["epoch"]["epoch_sha256"], current, name)
            failures.append(f"{name}: expected EpochMismatch, none raised — a bundle "
                            "from another epoch was silently accepted")
        except EpochMismatch:
            pass
        return bundle["verdict"], None, failures
    try:
        assert_same(bundle["epoch"]["epoch_sha256"], current, name)
    except EpochMismatch as exc:
        failures.append(f"{name}: {exc}")
        return bundle["verdict"], None, failures

    recorded = bundle["verdict"]
    if recorded in NON_EVALUATED:
        # Nothing to re-derive: assert the bundle carries the evidence that
        # justifies the refusal, which is the part that could rot.
        if recorded in ("skipped", "denied") and not bundle.get("preflight"):
            failures.append(f"{name}: {recorded} without a recorded pre-flight decision")
        if recorded == "aborted":
            if not bundle.get("abort"):
                failures.append(f"{name}: aborted without a recorded abort condition")
            steps = len(bundle.get("cleanup") or [])
            if steps != expected.get("cleanup_steps", steps):
                failures.append(f"{name}: cleanup log has {steps} steps, expected "
                                f"{expected['cleanup_steps']}")
        verdict, score = recorded, None
    else:
        v = evaluate(bundles.invariants(bundle), bundles.sample_set(bundle),
                     bundles.load_facts(bundle), float(bundle["load"]["min_rps_floor"]))
        verdict = v.verdict
        if verdict != expected["verdict"]:
            failures.append(f"{name}: verdict {verdict!r}, expected "
                            f"{expected['verdict']!r}")
        if "reason_contains" in expected and expected["reason_contains"] not in (
                (v.reason or "") + " ".join(
                    o.evidence.get("reason", "") for o in v.outcomes)):
            failures.append(f"{name}: reason {v.reason!r} does not mention "
                            f"{expected['reason_contains']!r}")
        for want in expected.get("falsified", []):
            got = [o.name for o in v.outcomes if o.outcome == "falsified"]
            if want not in got:
                failures.append(f"{name}: expected {want!r} falsified, got {got}")

        # Same coupling the runner applies, from the same function — so a
        # corpus case cannot certify behaviour the runner no longer has.
        result = scorer.calculate(
            scorer.checks_for_verdict(bundles.checks(bundle), verdict))
        score = result.score
        if expected.get("score") is None:
            if score is not None:
                failures.append(f"{name}: expected no score, got {score}")
        elif score is None or abs(score - expected["score"]) > SCORE_TOLERANCE:
            failures.append(f"{name}: score {score}, expected {expected['score']}")
        if "score_status" in expected and result.status != expected["score_status"]:
            failures.append(f"{name}: score status {result.status!r}, expected "
                            f"{expected['score_status']!r}")
        if "weights_denominator" in expected and (
                result.weights_denominator != expected["weights_denominator"]):
            failures.append(f"{name}: denominator {result.weights_denominator}, "
                            f"expected {expected['weights_denominator']}")
        if "excluded" in expected and sorted(result.excluded) != sorted(expected["excluded"]):
            failures.append(f"{name}: excluded {result.excluded}, expected "
                            f"{expected['excluded']}")

    return verdict, score, failures


def digest_of(name: str, verdict: str, score) -> str:
    payload = json.dumps({"case": name, "verdict": verdict,
                          "score": None if score is None else round(float(score), 6)},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(prog="replay_eval")
    ap.add_argument("--update-digests", action="store_true",
                    help="rewrite the frozen digest set. ONLY for a deliberate "
                         "scoring change, and the diff belongs in the same PR as "
                         "the change that moved it")
    args = ap.parse_args()

    corpus = load_corpus()
    failures: list[str] = []

    if len(corpus) < MIN_CORPUS_SIZE:
        failures.append(f"corpus has {len(corpus)} cases, minimum is {MIN_CORPUS_SIZE}")
    missing = REQUIRED_CASES - {c["name"] for c in corpus}
    if missing:
        failures.append(f"missing required regression cases: {sorted(missing)}")

    # Print the scoring inputs actually RESOLVED, not the ones on disk. A stale
    # .pyc can shadow an edited constants.py when the edit does not change the
    # file's length (0.35 -> 0.36 is the same size), and the digest comparison
    # would then be anchoring behaviour nobody can see in the source.
    print(f"scorer {scorer.SCORER_VERSION}  slo v{scorer.SLO_VERSION}  "
          f"epoch {scorer.epoch_sha256([])[:12]}")
    print("weights " + "  ".join(f"{k}={v}" for k, v in sorted(scorer.WEIGHTS.items())))
    print()
    print(f"{'CASE':46} {'VERDICT':11} {'SCORE':>8}")
    digests: dict[str, str] = {}
    for case in corpus:
        verdict, score, case_failures = replay(case)
        failures.extend(case_failures)
        digests[case["name"]] = digest_of(case["name"], verdict, score)
        shown = "none" if score is None else f"{score:.4f}"
        mark = " " if not case_failures else "!"
        print(f"{mark}{case['name']:45} {verdict:11} {shown:>8}")

    # --- the determinism anchor ------------------------------------------- #
    frozen_all = (json.loads(DIGEST_PATH.read_text(encoding="utf-8"))
                  if DIGEST_PATH.exists() else {})
    key = scorer.SCORER_VERSION

    if args.update_digests:
        frozen_all[key] = digests
        DIGEST_PATH.write_text(json.dumps(frozen_all, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(f"\nfroze {len(digests)} digests for SCORER_VERSION {key}")
        print("Commit this in the SAME PR as the change that moved it — that is "
              "what makes a scoring change visible in review.")
        return 0

    frozen = frozen_all.get(key)
    if frozen is None:
        failures.append(
            f"no frozen digests for SCORER_VERSION {key!r}. If the version was "
            "bumped deliberately, run `python -m evals.replay_eval --update-digests` "
            "and commit the result alongside the change.")
    else:
        for name, got in digests.items():
            want = frozen.get(name)
            if want is None:
                failures.append(f"{name}: no frozen digest — a new corpus case must "
                                "be frozen in the PR that adds it")
            elif want != got:
                failures.append(
                    f"{name}: DETERMINISM DIGEST MOVED ({want[:12]} -> {got[:12]}). "
                    "Either the scorer changed behaviour, or the case did. If the "
                    "change is deliberate, bump SCORER_VERSION, re-freeze, and put "
                    "both in this PR.")
        for stale in set(frozen) - set(digests):
            failures.append(f"{stale}: frozen digest with no corpus case — a "
                            "regression test was deleted rather than fixed")

    print()
    if failures:
        print(f"REPLAY EVAL: FAILED — {len(failures)} problem(s)")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    accuracy = 1.00
    print(f"REPLAY EVAL: {len(corpus)} cases, accuracy {accuracy:.2f} "
          f"(target {TARGET_ACCURACY:.2f}), {len(digests)} digests match "
          f"SCORER_VERSION {key}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
