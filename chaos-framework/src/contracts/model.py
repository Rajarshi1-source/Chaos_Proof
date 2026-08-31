"""The resilience contract model (§19.1).

A contract is a set of CLAIMS about failure behaviour: what a service promises
its consumers, what it absorbs from its dependencies, and what it promises not
to inflict on them. Every claim is addressable by a stable clause id, because
the whole mechanism rests on being able to say "this experiment falsified
THIS clause" rather than "an experiment failed".

Clause ids are the contract's API and must stay stable across versions:

    tolerates.payment-service.max_error_rate_pct
    provides.latency_p99_ms
    does_not_inflict[0]

Renaming one silently orphans its generated experiment and its recorded
validations, which is why they are derived mechanically from the document
structure rather than written by hand.
"""

import hashlib
import json
import pathlib
from dataclasses import dataclass, field

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
TARGET_APP = REPO_ROOT / "target-app"
CONTRACT_FILE = "resilience-contract.yaml"


@dataclass(frozen=True)
class RetryAmplification:
    attempts: int
    wait_ms: int
    effective_error_rate_pct_at_max: float | None = None
    worst_case_latency_ms: int | None = None

    def effective_error_rate(self, upstream_pct: float) -> float:
        """P(at least one attempt fails) = 1 - (1 - p)^attempts, as a percentage.

        This is the arithmetic the naive clause omits, and the reason
        "I tolerate a 5% error rate" is not the same claim as "5% of my calls
        will see an error". With three attempts a 5% upstream rate means 14.3%
        of logical calls experience at least one failed attempt — each of which
        costs a retry wait plus another timeout budget.
        """
        p = upstream_pct / 100.0
        return (1 - (1 - p) ** self.attempts) * 100.0

    def worst_case_latency(self, timeout_ms: int) -> int:
        """attempts x timeout + (attempts - 1) x wait."""
        return self.attempts * timeout_ms + (self.attempts - 1) * self.wait_ms

    def request_amplification(self, upstream_error_pct: float) -> float:
        """Expected downstream requests per logical call.

        Sum of the probability that attempt k is reached: 1 + p + p^2 + ...
        Distinct from the error arithmetic above and often confused with it —
        this one governs whether `does_not_inflict` survives, and it is much
        smaller than people expect at low error rates. The frightening number
        is the WORST case (every call retrying to exhaustion), which is what a
        correlated failure produces.
        """
        p = upstream_error_pct / 100.0
        return sum(p ** k for k in range(self.attempts))


@dataclass(frozen=True)
class Tolerates:
    dependency: str
    max_latency_ms: int | None = None
    max_error_rate_pct: float | None = None
    max_outage_seconds: int | None = None
    absorbed_by: str | None = None
    untestable_reason: str | None = None
    retry_amplification: RetryAmplification | None = None

    def clauses(self) -> list[tuple[str, str, float]]:
        """(clause_id, dimension, declared value) for each testable dimension."""
        out = []
        for field_name, dimension in (("max_latency_ms", "latency"),
                                      ("max_error_rate_pct", "error_rate"),
                                      ("max_outage_seconds", "outage")):
            value = getattr(self, field_name)
            if value is not None:
                out.append((f"tolerates.{self.dependency}.{field_name}",
                            dimension, float(value)))
        return out


@dataclass(frozen=True)
class Provides:
    availability_slo: float
    latency_p99_ms: int
    graceful_degradation_modes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DoesNotInflict:
    index: int
    clause: str
    metric: str | None = None
    comparator: str | None = None
    threshold: float | None = None

    @property
    def clause_id(self) -> str:
        return f"does_not_inflict[{self.index}]"

    @property
    def testable(self) -> bool:
        """A prose-only clause is a real promise but not a measurable one.
        Reported UNTESTED rather than quietly dropped — an unmeasurable
        guarantee that nobody notices is worse than one everybody can see."""
        return self.metric is not None and self.threshold is not None


@dataclass(frozen=True)
class Contract:
    service: str
    version: str
    provides: Provides
    tolerates: list[Tolerates]
    does_not_inflict: list[DoesNotInflict]
    owners: list[str] = field(default_factory=list)
    source_path: str | None = None
    raw: dict = field(default_factory=dict)

    def sha256(self) -> str:
        """Content address over the CLAIMS only — not the comments, not the key
        order, not the file path. Two files that make the same promises hash
        alike, so a reformatting commit does not read as a contract change."""
        return hashlib.sha256(
            json.dumps(canonical_claims(self.raw), sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()

    def clause_ids(self) -> list[str]:
        out = [f"provides.availability_slo", f"provides.latency_p99_ms"]
        for tol in self.tolerates:
            out.extend(cid for cid, _dim, _v in tol.clauses())
        out.extend(d.clause_id for d in self.does_not_inflict)
        return out


def canonical_claims(raw: dict) -> dict:
    """Only the fields that constitute a promise."""
    return {
        "service": raw.get("service"),
        "provides": raw.get("provides") or {},
        "tolerates": raw.get("tolerates") or [],
        "does_not_inflict": raw.get("does_not_inflict") or [],
    }


class ContractError(ValueError):
    pass


def parse(raw: dict, source_path: str | None = None) -> Contract:
    for required in ("service", "version", "provides"):
        if required not in raw:
            raise ContractError(f"contract is missing required key {required!r}")

    p = raw["provides"]
    provides = Provides(
        availability_slo=float(p["availability_slo"]),
        latency_p99_ms=int(p["latency_p99_ms"]),
        graceful_degradation_modes=list(p.get("graceful_degradation_modes") or []))

    tolerates = []
    # `tolerates` absent and `tolerates: []` are DIFFERENT claims: the first is
    # an omission, the second says "this is a leaf service, every failure it
    # exhibits originates here". Only the first is an error.
    if "tolerates" not in raw:
        raise ContractError(
            f"{raw['service']}: no `tolerates` key. If this service has no "
            "dependencies write `tolerates: []` — an empty list is a claim, a "
            "missing key is an oversight, and the generator must not have to "
            "guess which one it is looking at")
    for entry in raw["tolerates"] or []:
        amp = entry.get("retry_amplification")
        tolerates.append(Tolerates(
            dependency=entry["dependency"],
            max_latency_ms=entry.get("max_latency_ms"),
            max_error_rate_pct=entry.get("max_error_rate_pct"),
            max_outage_seconds=entry.get("max_outage_seconds"),
            absorbed_by=entry.get("absorbed_by"),
            untestable_reason=entry.get("untestable_reason"),
            retry_amplification=RetryAmplification(
                attempts=int(amp["attempts"]), wait_ms=int(amp["wait_ms"]),
                effective_error_rate_pct_at_max=amp.get("effective_error_rate_pct_at_max"),
                worst_case_latency_ms=amp.get("worst_case_latency_ms"),
            ) if amp else None))

    inflict = []
    for i, entry in enumerate(raw.get("does_not_inflict") or []):
        if isinstance(entry, str):
            inflict.append(DoesNotInflict(i, entry))
        else:
            inflict.append(DoesNotInflict(
                i, entry["clause"], entry.get("metric"), entry.get("comparator"),
                None if entry.get("threshold") is None else float(entry["threshold"])))

    return Contract(service=raw["service"], version=str(raw["version"]),
                    provides=provides, tolerates=tolerates,
                    does_not_inflict=inflict, owners=list(raw.get("owners") or []),
                    source_path=source_path, raw=raw)


def load(path: pathlib.Path) -> Contract:
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


def load_all(root: pathlib.Path | None = None) -> list[Contract]:
    root = root or TARGET_APP
    return [load(p) for p in sorted(root.glob(f"*/{CONTRACT_FILE}"))]


def for_service(service: str, root: pathlib.Path | None = None) -> Contract:
    root = root or TARGET_APP
    path = root / service / CONTRACT_FILE
    if not path.exists():
        raise ContractError(f"no contract at {path}")
    return load(path)
