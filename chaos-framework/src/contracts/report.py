"""Contract validation reporting (§19.2).

The report reads as ARCHITECTURE REVIEW rather than test output:

    CONTRACT VALIDATION - order-api v1.2.0        epoch 7f2a...  git 4c19ba2
      tolerates payment-service latency <= 800ms  -> P99 stayed 480ms   HONOURED
      tolerates payment-service errors   <= 5%    -> own errors 12.1%   VIOLATED
            |- retry budget amplifies: 3 attempts x 5% upstream = 14% effective.
               Reduce maxAttempts to 2 or add a retry budget cap.
      tolerates redis outage             <= 600s  -> not tested         UNTESTED

Three properties make this more than a nice YAML file, and all three live here:

  * UNTESTED IS REPORTED, so contract coverage is a metric and gaps are visible
    rather than invisible. A clause the framework cannot falsify still appears.
  * A VIOLATED CLAUSE NAMES THE MECHANISM where it can. "own errors 12.1% vs a
    5% claim" is a threshold breach; "your own retry policy turned a 5%
    dependency error rate into 14% of calls seeing a failure" is a finding.
  * PROVENANCE points every result back at the architectural claim it falsified.
"""

from dataclasses import dataclass, field

from .generator import Generated
from .model import Contract

HONOURED, VIOLATED, UNTESTED = "honoured", "violated", "untested"


@dataclass
class ClauseResult:
    clause: str
    summary: str                    # "tolerates payment-service errors <= 5%"
    outcome: str
    observed: str = ""
    mechanism: list[str] = field(default_factory=list)
    execution_id: int | None = None
    declared: float | None = None
    # UNTESTED splits in two, and conflating them hides the more important one:
    #   testable=True   nobody has run it yet. A scheduling gap; run it.
    #   testable=False  this framework CANNOT falsify it — a stateful target,
    #                   an absent dependency, a prose-only clause. No amount of
    #                   running experiments closes this one, and it is the gap
    #                   worth arguing about.
    testable: bool = True

    def to_row(self) -> dict:
        return {"clause": self.clause, "outcome": self.outcome,
                "observed": {"summary": self.observed,
                             "mechanism": self.mechanism,
                             "declared": self.declared}}


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def explain_retry_amplification(contract: Contract, dependency: str,
                                declared_pct: float, observed_error_pct: float,
                                timeout_ms: int = 3000) -> list[str]:
    """The project's best single result, as arithmetic anyone can check.

    Returns the explanatory lines for a violated error-rate clause when the
    consumer retries the dependency. Empty when no retry is declared — the
    mechanism is only named where it can be, never guessed at.
    """
    tol = next((t for t in contract.tolerates if t.dependency == dependency), None)
    if tol is None or tol.retry_amplification is None:
        return []

    amp = tol.retry_amplification
    effective = amp.effective_error_rate(declared_pct)
    worst_latency = amp.worst_case_latency(timeout_ms)
    load_mult = amp.request_amplification(declared_pct)

    lines = [
        f"retry budget amplifies: {amp.attempts} attempts against a "
        f"{_pct(declared_pct)} upstream error rate means "
        f"1 - (1 - {declared_pct / 100:.2f})^{amp.attempts} = {_pct(effective)} "
        f"of logical calls see AT LEAST ONE failed attempt.",
        f"each retried call costs up to {amp.wait_ms}ms of wait plus another "
        f"{timeout_ms}ms timeout budget; worst case {worst_latency}ms per call, "
        f"which is {worst_latency / contract.provides.latency_p99_ms:.0f}x the "
        f"{contract.provides.latency_p99_ms}ms this service publishes.",
        f"offered load on {dependency} rises to {load_mult:.3f}x under steady "
        f"state, and to {amp.attempts}x if failures correlate.",
    ]
    if observed_error_pct is not None:
        lines.append(
            f"observed {_pct(observed_error_pct)} against a declared "
            f"{_pct(declared_pct)}: the contract was violated BY ITS OWN RETRY "
            f"POLICY, not by the dependency exceeding what it promised.")
    lines.append(
        f"remedy: reduce maxAttempts to 2 (which would give "
        f"{_pct(amp.effective_error_rate(declared_pct) if amp.attempts == 2 else (1 - (1 - declared_pct / 100) ** 2) * 100)} "
        f"exposure), add a retry budget cap, or declare the amplified rate in "
        f"the contract so consumers are told the truth.")
    return lines


def explain_latency_amplification(contract: Contract, dependency: str,
                                  injected_ms: float, observed_p99_ms: float | None,
                                  ) -> list[str]:
    """Retry amplification in the LATENCY dimension.

    A retry does not only multiply requests, it multiplies DELAY: a call that
    needs k attempts costs k x injected + (k-1) x wait. That is why a service
    can honour "I tolerate 1000ms" in the error dimension (its fallback keeps
    availability at 100%) while breaking its own latency promise by 4x.

    UNITS ARE THE WHOLE POINT HERE. The first version of this function reused
    the error-rate explainer, which takes a PERCENTAGE — so a 1000ms clause was
    reported as a "1000% upstream error rate" producing "73000% of calls" and a
    remedy of "-8000% exposure". Every number was wrong and every number looked
    authoritative. A violated clause that names the wrong mechanism is worse
    than one that names none.
    """
    tol = next((t for t in contract.tolerates if t.dependency == dependency), None)
    if tol is None or tol.retry_amplification is None:
        return []

    amp = tol.retry_amplification
    promised = contract.provides.latency_p99_ms
    per_attempt = [(k, k * injected_ms + (k - 1) * amp.wait_ms)
                   for k in range(1, amp.attempts + 1)]

    lines = [
        f"retry budget amplifies DELAY, not just requests: with "
        f"{amp.attempts} attempts and a {amp.wait_ms}ms wait, a call needing k "
        f"attempts costs k x {injected_ms:.0f}ms + (k-1) x {amp.wait_ms}ms -> "
        + ", ".join(f"{k} attempt{'s' if k > 1 else ''} = {cost:.0f}ms"
                    for k, cost in per_attempt) + ".",
    ]
    if observed_p99_ms is not None:
        closest = min(per_attempt, key=lambda kc: abs(kc[1] - observed_p99_ms))
        lines.append(
            f"observed P99 {observed_p99_ms:.0f}ms sits closest to the "
            f"{closest[0]}-attempt cost of {closest[1]:.0f}ms, so a material "
            f"fraction of calls retried.")
        lines.append(
            f"that is {observed_p99_ms / promised:.1f}x the {promised}ms this "
            f"service publishes, from a dependency delay it declared it "
            f"TOLERATED: the contract was violated BY ITS OWN RETRY POLICY, "
            f"not by {dependency} exceeding what it promised.")
    lines.append(
        f"remedy: reduce maxAttempts to 2 (worst case "
        f"{2 * injected_ms + amp.wait_ms:.0f}ms), add a per-call retry deadline "
        f"so total time is bounded rather than per-attempt, or publish a "
        f"latency_p99_ms that accounts for the retry budget.")
    return lines


@dataclass
class ContractReport:
    contract: Contract
    results: list[ClauseResult]
    epoch: str | None = None
    git_sha: str | None = None

    @property
    def tested(self) -> int:
        """Clauses with a real outcome from a real run."""
        return sum(1 for r in self.results if r.outcome != UNTESTED)

    @property
    def testable(self) -> int:
        """Clauses this framework could falsify if someone ran them."""
        return sum(1 for r in self.results if r.testable)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def coverage_ratio(self) -> float:
        """`chaosproof_contract_clause_coverage_ratio` — validated over
        DECLARED, not over testable. Dividing by the testable subset would let a
        service improve its coverage by making a clause unfalsifiable, which is
        exactly backwards."""
        return self.tested / self.total if self.total else 0.0

    @property
    def testable_ratio(self) -> float:
        return self.testable / self.total if self.total else 0.0

    @property
    def violated(self) -> list[ClauseResult]:
        return [r for r in self.results if r.outcome == VIOLATED]

    @property
    def unfalsifiable(self) -> list[ClauseResult]:
        return [r for r in self.results if not r.testable]

    def render(self) -> str:
        c = self.contract
        head = f"CONTRACT VALIDATION - {c.service} v{c.version}"
        stamp = "  ".join(x for x in (
            f"epoch {self.epoch[:12]}" if self.epoch else "",
            f"git {self.git_sha[:7]}" if self.git_sha else "") if x)
        lines = [f"{head}{'':>{max(1, 50 - len(head))}}{stamp}".rstrip()]

        width = max((len(r.summary) for r in self.results), default=0)
        for r in self.results:
            observed = r.observed or "not run"
            if len(observed) > 30:
                observed = observed[:27] + "..."
            lines.append(f"  {r.summary:<{width}}  -> {observed:<30} "
                         f"{r.outcome.upper()}")
            for i, line in enumerate(r.mechanism):
                prefix = "        |- " if i == 0 else "           "
                lines.append(f"{prefix}{line}")

        lines.append("")
        lines.append(f"  coverage {self.tested}/{self.total} clauses validated "
                     f"({self.coverage_ratio:.0%})   "
                     f"violated {len(self.violated)}")
        unfalsifiable = self.unfalsifiable
        if unfalsifiable:
            lines.append(f"  {len(unfalsifiable)} clause(s) this framework CANNOT "
                         f"falsify at all:")
            for r in unfalsifiable:
                lines.append(f"    - {r.summary}: {r.observed}")
            lines.append("    Listed rather than dropped. Running more experiments "
                         "does not close these, which is what makes them the gap "
                         "worth arguing about.")
        return "\n".join(lines)

    def prometheus_metrics(self) -> str:
        """`chaosproof_contract_clause_coverage_ratio` — the metric that makes
        gaps visible on a dashboard rather than only in a CLI run."""
        svc = self.contract.service
        return "\n".join([
            "# HELP chaosproof_contract_clause_coverage_ratio Fraction of "
            "resilience-contract clauses this framework can actually falsify.",
            "# TYPE chaosproof_contract_clause_coverage_ratio gauge",
            f'chaosproof_contract_clause_coverage_ratio{{service="{svc}"}} '
            f"{self.coverage_ratio:.4f}",
            "# HELP chaosproof_contract_clauses_total Clauses declared.",
            "# TYPE chaosproof_contract_clauses_total gauge",
            f'chaosproof_contract_clauses_total{{service="{svc}"}} {self.total}',
            "# HELP chaosproof_contract_clauses_violated Clauses falsified by "
            "the most recent validation.",
            "# TYPE chaosproof_contract_clauses_violated gauge",
            f'chaosproof_contract_clauses_violated{{service="{svc}"}} '
            f"{len(self.violated)}",
            "# HELP chaosproof_contract_clauses_falsifiable_ratio Fraction of "
            "clauses this framework is structurally able to falsify. Distinct "
            "from coverage: running more experiments cannot raise this one.",
            "# TYPE chaosproof_contract_clauses_falsifiable_ratio gauge",
            f'chaosproof_contract_clauses_falsifiable_ratio{{service="{svc}"}} '
            f"{self.testable_ratio:.4f}",
        ])


def summarise_clause(g: Generated) -> str:
    """The human-readable left-hand column."""
    clause = g.provenance.clause
    if clause.startswith("does_not_inflict"):
        return f"does_not_inflict {g.spec.get('clause', clause)}"
    parts = clause.split(".")
    if len(parts) == 3 and parts[0] == "tolerates":
        _, dep, field_name = parts
        dimension = {"max_latency_ms": "latency", "max_error_rate_pct": "errors",
                     "max_outage_seconds": "outage"}.get(field_name, field_name)
        unit = {"max_latency_ms": "ms", "max_error_rate_pct": "%",
                "max_outage_seconds": "s"}.get(field_name, "")
        return f"tolerates {dep} {dimension} <= {g.provenance.declared:g}{unit}"
    return clause


def build(contract: Contract, generated: list[Generated],
          outcomes: dict[str, ClauseResult] | None = None,
          epoch: str | None = None, git_sha: str | None = None) -> ContractReport:
    """Assemble a report. Clauses with no recorded outcome are UNTESTED — the
    default is 'we have not checked this', never 'this is fine'."""
    outcomes = outcomes or {}
    results = []
    for g in generated:
        clause = g.provenance.clause
        if clause in outcomes:
            r = outcomes[clause]
            r.summary = r.summary or summarise_clause(g)
            r.declared = g.provenance.declared
            results.append(r)
            continue
        results.append(ClauseResult(
            clause=clause, summary=summarise_clause(g), outcome=UNTESTED,
            observed=(g.untested_reason or "not run yet"),
            declared=g.provenance.declared, testable=g.testable))
    return ContractReport(contract, results, epoch, git_sha)
