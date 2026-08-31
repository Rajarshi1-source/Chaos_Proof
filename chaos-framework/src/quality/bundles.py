"""Content-addressed evidence bundles (§22.1).

Every execution produces one bundle holding everything needed to re-derive its
verdict and its score: the hypothesis, the load-run summary and script hash,
every SLI sample from both sources, the check results, the verdict, the epoch,
the cleanup log, and the `git_sha`. The sha256 of its canonical JSON IS the
run's identity.

Bundles make three otherwise-impossible things possible:

  * retro-scoring under a new epoch (the raw samples are still there),
  * the hermetic replay eval (no cluster, no network, no database),
  * offline reproduction via `chaosctl replay <sha>`.

CANONICALISATION IS LOAD-BEARING. The digest anchors the determinism gate, so
two structurally identical bundles must serialise byte-identically: sorted
keys, no incidental whitespace, and floats rounded to a fixed precision. That
last one is not fussiness — an unrounded float differing in its 17th decimal
between two machines would break the digest with no behaviour change at all,
and a determinism gate that fires on platform noise is a gate people disable.

Bundles are immutable and write-once. There is no `update_bundle`.
"""

import hashlib
import json

SCHEMA_VERSION = 1

# 6 decimal places. Scores are reported to 4, SLI values to 4; 6 leaves room
# without exposing float representation noise.
FLOAT_PRECISION = 6


def _round(obj):
    """Recursively round floats so serialisation is stable across platforms."""
    if isinstance(obj, float):
        return round(obj, FLOAT_PRECISION)
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def canonical(bundle: dict) -> str:
    """The one serialisation the digest is taken over."""
    return json.dumps(_round(bundle), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def digest(bundle: dict) -> str:
    """The bundle's identity. Computed over the bundle WITHOUT any existing
    `bundle_sha256` field, so a bundle can carry its own digest without the
    digest depending on itself."""
    body = {k: v for k, v in bundle.items() if k != "bundle_sha256"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def seal(bundle: dict) -> dict:
    """Return the bundle with its digest attached. Idempotent."""
    out = dict(bundle)
    out.pop("bundle_sha256", None)
    out["bundle_sha256"] = digest(out)
    return out


def verify(bundle: dict) -> bool:
    """Re-hash and compare. `chaosctl audit --verify-bundles` is this in a loop."""
    claimed = bundle.get("bundle_sha256")
    return bool(claimed) and claimed == digest(bundle)


def build(*, experiment: str, hypothesis: dict, load: dict, samples: list[dict],
          checks: list[dict], verdict: str, verdict_reason: str | None,
          score: dict | None, epoch: dict, cleanup: list[dict] | None = None,
          git_sha: str | None = None, abort: dict | None = None,
          preflight: dict | None = None, chaos_result: dict | None = None,
          invariant_outcomes: list[dict] | None = None,
          blast_radius: dict | None = None,
          execution_id: int | None = None) -> dict:
    """Assemble and seal. Keyword-only: a bundle assembled from positional
    arguments is one refactor away from silently swapping two fields, and the
    digest would happily hash the wrong thing."""
    return seal({
        "schema_version": SCHEMA_VERSION,
        "experiment": experiment,
        "hypothesis": hypothesis,
        "load": load,
        "samples": samples,
        "checks": checks,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "score": score,
        "epoch": epoch,
        "cleanup": cleanup or [],
        "abort": abort,
        "preflight": preflight,
        "chaos_result": chaos_result,
        # PER-INVARIANT outcomes, not one blob. The verdict is the headline; the
        # outcomes are what makes it checkable, and `chaosctl replay` renders
        # them line by line.
        "invariant_outcomes": invariant_outcomes or [],
        "blast_radius": blast_radius,
        # The execution id is an INDEX into the evidence store, deliberately not
        # part of the run's identity in any other sense: the digest is over the
        # evidence, so two identical runs would hash alike regardless of which
        # row they happened to land in.
        "execution_id": execution_id,
        "git_sha": git_sha,
    })


# --------------------------------------------------------------------------- #
# Reconstruction — the half that makes a bundle worth storing.
# --------------------------------------------------------------------------- #

def sample_set(bundle: dict):
    """Rebuild a SampleSet from the bundle's stored ticks.

    Imported lazily so this module stays importable in contexts that only need
    hashing (bundle verification in CI, for instance).
    """
    from ..measurement.sampler import Sample, SampleSet

    ss = SampleSet()
    for tick in bundle.get("samples") or []:
        ss.add(Sample(
            sampled_at=float(tick["t"]),
            client=dict(tick.get("client") or {}),
            server=dict(tick.get("server") or {}),
        ))
    return ss


def load_facts(bundle: dict):
    from ..measurement.validity import LoadFacts

    load = bundle.get("load") or {}
    return LoadFacts(
        achieved_rps=float(load.get("achieved_rps") or 0.0),
        dropped_iterations=float(load.get("dropped_iterations") or 0.0),
        coverage=float(load.get("coverage") or 0.0),
    )


def invariants(bundle: dict):
    from ..hypothesis.engine import parse_invariants

    return parse_invariants(bundle["hypothesis"]["invariants"])


def checks(bundle: dict):
    from ..scoring.scorer import Check

    return [Check(
        check_type=c["check_type"], check_name=c.get("check_name", c["check_type"]),
        applicable=bool(c["applicable"]), outcome=c["outcome"],
        score=(None if c.get("score") is None else float(c["score"])),
        expected_value=c.get("expected_value"), actual_value=c.get("actual_value"),
        message=c.get("message", ""), details=c.get("details") or {},
    ) for c in bundle.get("checks") or []]


def ticks_from_samples(samples) -> list[dict]:
    """SampleSet -> the bundle's `samples` shape."""
    return [{"t": s.sampled_at,
             "client": {k: v for k, v in s.client.items() if v is not None},
             "server": {k: v for k, v in s.server.items() if v is not None}}
            for s in samples.samples]
