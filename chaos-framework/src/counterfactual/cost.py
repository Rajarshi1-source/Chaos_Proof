"""Cost translation (§17.2) — with its assumptions attached, never buried.

    "₹3,000/year, under these three assumptions which are on the dashboard"

is credible. An unqualified rupee figure invites an interviewer to dismantle
it, and they will be right to: the number is downstream of estimates that are
doing more work than the measurement is.

So every input carries a `label` and a `basis`, they are rendered ON the panel
rather than in a footnote, and they are EDITABLE — a reader who disagrees with
₹450 per checkout can put their own figure in and watch the conclusion move.
That is a much stronger position than defending a number.

The one rule with teeth: a cost figure is only ever produced from a
CounterfactualResult that supports a delta. An `inconclusive` result has no
delta, so it has no cost — the arithmetic simply does not run.
"""

from dataclasses import dataclass, field

from .analysis import CounterfactualResult

CURRENCY = "INR"
SYMBOL = "₹"


@dataclass
class Assumption:
    key: str
    value: float
    label: str
    basis: str
    unit: str = ""
    editable: bool = True

    def render_value(self) -> str:
        """A currency symbol PREFIXES; every other unit suffixes. Rendering
        `fraction1` and `count12` is the kind of small wrongness that makes a
        reader stop trusting the panel it appears on."""
        if self.unit == SYMBOL:
            return f"{SYMBOL}{self.value:g}"
        return f"{self.value:g}{(' ' + self.unit) if self.unit else ''}"

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "label": self.label,
                "basis": self.basis, "unit": self.unit, "editable": self.editable,
                # The UI renders this verbatim next to the input. It is not
                # decoration: an estimate that is not labelled an estimate is
                # being passed off as a measurement.
                "tag": "[assumption]"}


def default_assumptions() -> list[Assumption]:
    return [
        Assumption("revenue_per_checkout", 450.0,
                   "revenue per successful checkout",
                   "median order value, order-api sample data", SYMBOL),
        Assumption("conversion_loss_per_failed_request", 1.0,
                   "conversion loss per failed request",
                   "pessimistic: assumes every failed checkout is a lost sale "
                   "rather than a retry by the user", "fraction"),
        Assumption("incidents_per_year", 12.0,
                   "incidents of this class per year",
                   "extrapolated from 6 months of incident data — the weakest "
                   "of the three, and the one most worth arguing about", "count"),
    ]


@dataclass
class CostEstimate:
    pattern: str
    requests_saved_per_incident: float
    value_per_incident: float
    value_per_year: float
    currency: str = CURRENCY
    assumptions: list[Assumption] = field(default_factory=default_assumptions)
    caveat: str = ""

    def to_dict(self) -> dict:
        return {"pattern": self.pattern,
                "requests_saved_per_incident": self.requests_saved_per_incident,
                "value_per_incident": self.value_per_incident,
                "value_per_year": self.value_per_year,
                "currency": self.currency, "caveat": self.caveat,
                "assumptions": [a.to_dict() for a in self.assumptions]}


def estimate(result: CounterfactualResult,
             assumptions: list[Assumption] | None = None) -> CostEstimate | None:
    """Translate a counterfactual delta into money, or return None.

    None is the correct output whenever the analysis does not support a delta.
    There is deliberately no "best effort" path and no fallback to the raw
    median difference: a cost figure attached to an `inconclusive` result would
    be the most persuasive wrong number this system could produce, because
    currency reads as precision.
    """
    delta = result.delta_median
    if delta is None or delta <= 0:
        return None

    values = {a.key: a.value for a in (assumptions or default_assumptions())}
    revenue = values["revenue_per_checkout"]
    loss_fraction = values["conversion_loss_per_failed_request"]
    incidents = values["incidents_per_year"]

    per_incident = delta * revenue * loss_fraction
    return CostEstimate(
        pattern=result.pattern,
        requests_saved_per_incident=delta,
        value_per_incident=per_incident,
        value_per_year=per_incident * incidents,
        assumptions=list(assumptions or default_assumptions()),
        caveat=(f"n={result.n} per arm. This figure is the median delta "
                f"({delta:.0f} requests) priced with three assumptions shown "
                f"alongside it; change any of them and the figure moves."))


def render(result: CounterfactualResult, cost: CostEstimate | None) -> str:
    """The CLI form of the ROI panel."""
    lines = [f"PATTERN {result.pattern}   metric {result.metric}   n={result.n} per arm"]

    if result.with_median is None:
        lines.append(f"  {result.verdict.upper()}: {result.reason}")
        return "\n".join(lines)

    lines.append(f"  with pattern     median {result.with_median:>10.4g}   "
                 f"IQR {result.with_iqr[0]:.4g} - {result.with_iqr[1]:.4g}")
    lines.append(f"  without pattern  median {result.without_median:>10.4g}   "
                 f"IQR {result.without_iqr[0]:.4g} - {result.without_iqr[1]:.4g}")

    if result.delta_median is None:
        lines.append(f"  VERDICT: {result.verdict.upper()} — NO DELTA REPORTED")
        lines.append(f"           {result.reason}")
        return "\n".join(lines)

    lines.append(f"  delta (median)   {result.delta_median:>10.4g}")
    lines.append(f"  VERDICT: {result.verdict.upper()} — {result.reason}")
    if cost is not None:
        lines.append(f"  value  {SYMBOL}{cost.value_per_incident:,.0f} per incident, "
                     f"{SYMBOL}{cost.value_per_year:,.0f} per year")
        lines.append(f"         {cost.caveat}")
        for a in cost.assumptions:
            lines.append(f"           [assumption] {a.label} = {a.render_value()}"
                         f"  ({a.basis})")
    return "\n".join(lines)
