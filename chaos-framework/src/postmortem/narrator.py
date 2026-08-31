"""Postmortem generation, with a hard boundary (§22.2).

Four rules, in priority order, and the order is the argument:

  1. `TemplateNarrator` IS THE DEFAULT. The system works with no API key and no
     network. A portfolio project that breaks without a paid API is a liability
     in a live demo, and a postmortem generator that cannot run offline cannot
     run beside `chaosctl replay`, which is the whole point of Phase 11.
  2. PROVIDER-AGNOSTIC ADAPTER, model id from config. Never a vendor hardcoded
     at the call site.
  3. THE GROUNDING CHECK IS NOT OPTIONAL. Every number in a draft must appear
     in the bundle. A draft that invents one is DISCARDED, and the discard is
     logged and counted in `chaosproof_narrator_discards_total`. A hallucinated
     postmortem is worse than a templated one precisely because it is more
     convincing.
  4. THE LLM NEVER TOUCHES A VERDICT. It drafts prose a human then edits.

    "The decision layer is deterministic rules — I need to unit-test it, replay
     it, and hash it. The LLM writes the prose, and if it mentions a number that
     isn't in the evidence bundle the draft is thrown away. In 2026 everyone
     claims an AI SRE agent; 'my scoring is deterministic and my writing is
     AI-assisted' is the more defensible position."
"""

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Counted, not just logged. A discard rate that climbs is a signal about the
# model or the prompt, and it belongs on a dashboard rather than in a log file
# nobody greps.
DISCARDS = {"total": 0, "by_reason": {}}

# Numbers that are structural rather than claims about the run: version
# numbers, percentages of 100, small ordinals in prose. Allowing them keeps the
# grounding check from rejecting "all 4 checks" while still catching an
# invented latency figure.
STRUCTURAL = {"0", "1", "2", "3", "4", "5", "100"}

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _record_discard(reason: str) -> None:
    DISCARDS["total"] += 1
    DISCARDS["by_reason"][reason] = DISCARDS["by_reason"].get(reason, 0) + 1


def metrics() -> str:
    lines = [
        "# HELP chaosproof_narrator_discards_total Drafts discarded for "
        "referencing a number absent from the evidence bundle.",
        "# TYPE chaosproof_narrator_discards_total counter",
        f"chaosproof_narrator_discards_total {DISCARDS['total']}",
    ]
    for reason, count in sorted(DISCARDS["by_reason"].items()):
        lines.append(
            f'chaosproof_narrator_discards_by_reason{{reason="{reason}"}} {count}')
    return "\n".join(lines)


def bundle_numbers(bundle: dict) -> set[str]:
    """Every number that appears anywhere in the bundle, as text.

    Deliberately generous — it walks the whole structure — because the check is
    meant to catch INVENTION, not to police rounding. A draft that says 119.4
    when the bundle says 119.4 passes; one that says 143 when nothing in the
    bundle is 143 does not.
    """
    found: set[str] = set()

    def walk(node):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.add(_normalise(node))
            # Rounded forms a human would write: 0.9942 -> 0.99, 119.4 -> 119.
            found.add(_normalise(round(float(node), 2)))
            found.add(_normalise(round(float(node))))
            # Percentage forms: 0.9942 -> 99.42
            if 0 <= float(node) <= 1:
                found.add(_normalise(round(float(node) * 100, 2)))
                found.add(_normalise(round(float(node) * 100, 1)))
                found.add(_normalise(round(float(node) * 100)))
            return
        if isinstance(node, str):
            for match in NUMBER.findall(node):
                found.add(_normalise(match))
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
            return
        if isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(bundle)
    return found | STRUCTURAL


def _normalise(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def ungrounded_numbers(text: str, bundle: dict) -> list[str]:
    """Numbers in `text` that do not appear in the bundle."""
    allowed = bundle_numbers(bundle)
    return [n for n in NUMBER.findall(text) if _normalise(n) not in allowed]


@dataclass
class Draft:
    summary: str
    contributing_factors: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    source: str = "template"
    discarded: bool = False
    discard_reason: str | None = None

    def render(self) -> str:
        lines = [f"# Postmortem draft ({self.source})", "", self.summary]
        if self.contributing_factors:
            lines += ["", "## Contributing factors", ""]
            lines += [f"- {f}" for f in self.contributing_factors]
        if self.action_items:
            lines += ["", "## Action items", ""]
            lines += [f"- {a}" for a in self.action_items]
        return "\n".join(lines)


class Narrator:
    """Drafts prose from the bundle. NEVER produces or alters a verdict, score,
    or number."""

    name = "narrator"

    def draft(self, bundle: dict) -> Draft:
        raise NotImplementedError


class TemplateNarrator(Narrator):
    """THE DEFAULT. Deterministic, offline, no API key.

    Every sentence is assembled from bundle fields, so it cannot hallucinate by
    construction — there is no generation step to hallucinate in. It is also
    what makes `chaosctl replay` and a postmortem work on the same laptop with
    the wifi off.
    """

    name = "template"

    def draft(self, bundle: dict) -> Draft:
        verdict = str(bundle.get("verdict", "unknown"))
        experiment = bundle.get("experiment", "unknown")
        score = (bundle.get("score") or {}).get("score")
        load = bundle.get("load") or {}
        outcomes = bundle.get("invariant_outcomes") or []

        falsified = [o for o in outcomes if o.get("outcome") == "falsified"]
        invalid = [o for o in outcomes if o.get("outcome") == "invalid"]

        score_text = "no score (not scoreable)" if score is None else f"score {score}"
        summary = (
            f"Experiment `{experiment}` returned **{verdict.upper()}** with "
            f"{score_text}. The load plane achieved "
            f"{load.get('achieved_rps')} rps against a floor of "
            f"{load.get('min_rps_floor')}, with "
            f"{load.get('dropped_iterations')} dropped iterations.")

        factors = []
        for o in falsified:
            factors.append(
                f"`{o.get('name')}` was falsified: worst {o.get('worst_value')} "
                f"against a threshold of {o.get('threshold')}, breached for "
                f"{o.get('breached_for_s')}s.")
        for o in invalid:
            factors.append(
                f"`{o.get('name')}` could not be measured — "
                f"{(o.get('evidence') or {}).get('reason', 'no samples')}. "
                f"This is not a failure of the system; it is a failure to observe it.")
        if bundle.get("abort"):
            abort = bundle["abort"]
            factors.append(
                f"The run was ABORTED by {abort.get('path')} on "
                f"{abort.get('condition')}, so the hypothesis was never given "
                f"the full conditions it describes.")

        actions = []
        if falsified:
            actions.append("Decide whether the threshold or the system is wrong. "
                           "Widening a tolerance to make an experiment pass "
                           "produces an experiment that cannot fail.")
        if invalid:
            actions.append("Fix the measurement plane before re-running: an "
                           "unmeasurable run carries no information about "
                           "resilience either way.")
        if not falsified and not invalid:
            actions.append("No action indicated by this run.")

        return Draft(summary, factors, actions, source="template")


class LLMNarrator(Narrator):
    """Provider-agnostic. The model id comes from config, never a call site.

    Output is DISCARDED if it references any number not in the bundle, and the
    discard falls back to the template. That fallback is why this class is safe
    to enable: the worst case is deterministic prose, never a confident
    invention.
    """

    name = "llm"

    def __init__(self, complete=None, model: str | None = None,
                 fallback: Narrator | None = None):
        self._complete = complete
        self.model = model
        self.fallback = fallback or TemplateNarrator()

    def draft(self, bundle: dict) -> Draft:
        if self._complete is None:
            # No provider configured is the NORMAL case, not an error.
            return self.fallback.draft(bundle)

        try:
            text = self._complete(self._prompt(bundle), model=self.model)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("narrator provider failed (%s); falling back to template",
                        type(exc).__name__)
            _record_discard("provider_error")
            draft = self.fallback.draft(bundle)
            draft.discarded = True
            draft.discard_reason = f"provider error: {type(exc).__name__}"
            return draft

        ungrounded = ungrounded_numbers(text or "", bundle)
        if ungrounded:
            # A hallucinated postmortem is worse than a templated one because it
            # is more convincing. Discard, count, fall back.
            log.warning("narrator draft discarded: numbers not in bundle: %s",
                        sorted(set(ungrounded))[:6])
            _record_discard("ungrounded_number")
            draft = self.fallback.draft(bundle)
            draft.discarded = True
            draft.discard_reason = (
                "draft referenced numbers absent from the evidence bundle: "
                + ", ".join(sorted(set(ungrounded))[:6]))
            return draft

        return Draft(summary=text.strip(), source=f"llm:{self.model or 'unset'}")

    def _prompt(self, bundle: dict) -> str:
        return (
            "Write a short postmortem summary for this chaos experiment. "
            "Use ONLY numbers that appear in the evidence below; do not "
            "estimate, round beyond what is shown, or introduce any figure of "
            "your own. Do not state a verdict — it is given.\n\n"
            f"verdict: {bundle.get('verdict')}\n"
            f"experiment: {bundle.get('experiment')}\n"
            f"invariants: {bundle.get('invariant_outcomes')}\n"
            f"load: {bundle.get('load')}\n")


def default() -> Narrator:
    """TemplateNarrator, always, unless a caller deliberately builds otherwise."""
    return TemplateNarrator()
