"""Scoring epochs — the versioned model artefact (§21.1).

A resilience score is only comparable to another score computed the same way.
Rev 1's 45%->92% story spanned four weeks in which alert rules were added
(changing what `alert_validation` could pass) and experiments were tuned, so
part of that "improvement" was edits to the scorer rather than changes to the
system. The epoch is what makes that visible instead of deniable.

    epoch = sha256(weights || gating experiment set || SLO version || scorer version)

Three properties, all mandatory:

  1. A trend line may only connect points WITHIN one epoch.
  2. Epochs are IMMUTABLE. A corrected score is a new row under a new epoch
     referencing the same bundle — never an UPDATE. That is the difference
     between retro-scoring and revisionism.
  3. Anything that changes the MEANING of a score opens one: the weights, the
     gating experiment set, an SLO threshold, the scorer code, or any constant
     in the threshold module.

Note the third input, because it is the one people forget: the GATING
EXPERIMENT SET. Quarantining a flaky experiment removes it from that set and
therefore opens a new epoch — which is correct, because the composite score is
now an average over different things.
"""

from dataclasses import dataclass

from ..scoring import scorer


@dataclass(frozen=True)
class Epoch:
    sha256: str
    weights: dict
    experiment_set: list[str]
    slo_version: int
    scorer_version: str
    change_reason: str | None = None
    id: int | None = None

    def short(self) -> str:
        return self.sha256[:12]

    def to_dict(self) -> dict:
        return {"epoch_sha256": self.sha256, "weights": self.weights,
                "experiment_set": list(self.experiment_set),
                "slo_version": self.slo_version,
                "scorer_version": self.scorer_version,
                "change_reason": self.change_reason}


def compute(gating_experiments: list[str], change_reason: str | None = None) -> Epoch:
    """The epoch implied by the CURRENT code and the given gating set.

    Pure: no database, no clock, no environment. The replay eval depends on
    that — an epoch that varied with wall time could not anchor a determinism
    digest.
    """
    material = scorer.epoch_material(gating_experiments)
    return Epoch(
        sha256=scorer.epoch_sha256(gating_experiments),
        weights=material["weights"],
        experiment_set=material["experiment_set"],
        slo_version=material["slo_version"],
        scorer_version=material["scorer_version"],
        change_reason=change_reason,
    )


class EpochMismatch(Exception):
    """Refusing to score a bundle under an epoch it was not measured under.

    This is a REFUSAL, not a coercion. Silently re-scoring under whatever epoch
    happens to be current is precisely how a trend chart becomes fiction: the
    numbers all look reasonable and none of them are comparable. If a bundle
    genuinely needs a score under a new epoch, that is retro-scoring — an
    explicit operation that writes a NEW row and says so.
    """

    def __init__(self, bundle_epoch: str, current_epoch: str, detail: str = ""):
        self.bundle_epoch = bundle_epoch
        self.current_epoch = current_epoch
        super().__init__(
            f"bundle was scored under epoch {bundle_epoch[:12]} but the current "
            f"epoch is {current_epoch[:12]}"
            + (f" ({detail})" if detail else "")
            + " — refusing to compare. Use retro-scoring to produce a new score "
              "under the new epoch; it writes a new row rather than rewriting this one.")


def assert_same(bundle_epoch: str, current_epoch: str, detail: str = "") -> None:
    if bundle_epoch != current_epoch:
        raise EpochMismatch(bundle_epoch, current_epoch, detail)


def diff(a: Epoch, b: Epoch) -> list[str]:
    """What actually changed between two epochs, in the words a change_reason
    should use. A boundary on the trend chart labelled 'scorer changed' teaches
    nobody anything; 'gating set +pod_kill_payment_svc' does."""
    out: list[str] = []
    if a.weights != b.weights:
        moved = sorted(k for k in set(a.weights) | set(b.weights)
                       if a.weights.get(k) != b.weights.get(k))
        out.append("weights " + ", ".join(
            f"{k} {a.weights.get(k)}->{b.weights.get(k)}" for k in moved))
    if a.experiment_set != b.experiment_set:
        added = sorted(set(b.experiment_set) - set(a.experiment_set))
        removed = sorted(set(a.experiment_set) - set(b.experiment_set))
        parts = []
        if added:
            parts.append("+" + ", +".join(added))
        if removed:
            parts.append("-" + ", -".join(removed))
        out.append("gating set " + " ".join(parts))
    if a.slo_version != b.slo_version:
        out.append(f"slo_version {a.slo_version}->{b.slo_version}")
    if a.scorer_version != b.scorer_version:
        out.append(f"scorer_version {a.scorer_version}->{b.scorer_version}")
    return out


# --------------------------------------------------------------------------- #
# Persistence. Everything above is pure; everything below touches the store.
# --------------------------------------------------------------------------- #

def gating_experiments(cur) -> list[str]:
    """The gating set as the evidence store currently records it. This is the
    input that flakiness quarantine moves, and moving it opens an epoch."""
    cur.execute("SELECT t.name FROM experiment_types t "
                "JOIN experiment_flakiness f ON f.experiment_type_id = t.id "
                "WHERE f.gating IS TRUE ORDER BY t.name")
    return [r[0] for r in cur.fetchall()]


def current(cur) -> Epoch:
    return compute(gating_experiments(cur))


def open_epoch(cur, change_reason: str, gating: list[str] | None = None) -> Epoch:
    """Record the epoch implied by the current code, if it is not already
    recorded. Idempotent by sha256.

    There is deliberately no `update_epoch`. A closed epoch is never edited —
    that is the whole point, and the absence of the function is the enforcement.
    """
    gating = gating_experiments(cur) if gating is None else gating
    epoch = compute(gating, change_reason)
    cur.execute(
        """INSERT INTO scoring_epochs (epoch_sha256, weights, experiment_set,
                                       slo_version, scorer_version, change_reason)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (epoch_sha256) DO NOTHING""",
        (epoch.sha256, _json(epoch.weights), epoch.experiment_set,
         epoch.slo_version, epoch.scorer_version, change_reason))
    cur.execute("SELECT id, change_reason FROM scoring_epochs WHERE epoch_sha256 = %s",
                (epoch.sha256,))
    row = cur.fetchone()
    return Epoch(epoch.sha256, epoch.weights, epoch.experiment_set,
                 epoch.slo_version, epoch.scorer_version, row[1], row[0])


def history(cur) -> list[Epoch]:
    cur.execute("""SELECT id, epoch_sha256, weights, experiment_set, slo_version,
                          scorer_version, change_reason
                     FROM scoring_epochs ORDER BY id""")
    return [Epoch(r[1], r[2], list(r[3] or []), r[4], r[5], r[6], r[0])
            for r in cur.fetchall()]


def _json(obj) -> str:
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
