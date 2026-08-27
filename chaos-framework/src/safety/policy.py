"""Declarative safety policy, evaluated with cel-python.

First DENY wins, then REQUIRE_OVERRIDE, else ALLOW. The rules live in
policy/chaos_safety.yaml so adding a guardrail is a reviewable pull request
rather than a code change.

A rule that cannot be evaluated is treated as a DENY, never as an ALLOW: the
same fail-closed instinct that makes a missing series INVALID rather than a
pass. A guardrail that silently disappears when its expression breaks is not a
guardrail.
"""

import pathlib
from dataclasses import dataclass

import yaml

try:                                            # pragma: no cover - import shape
    import celpy
    HAVE_CEL = True
except ImportError:                             # pragma: no cover
    HAVE_CEL = False

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
POLICY_PATH = REPO_ROOT / "policy" / "chaos_safety.yaml"


@dataclass(frozen=True)
class Decision:
    effect: str                 # allow | deny | require_override
    rule_id: str | None = None
    message: str | None = None

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"


def load_rules(path: pathlib.Path = POLICY_PATH) -> list[dict]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc.get("rules", [])


def _evaluate_expr(expr: str, context: dict) -> bool:
    """True when the rule MATCHES (i.e. its effect should apply)."""
    if not HAVE_CEL:
        raise RuntimeError(
            "cel-python is not installed; the policy engine cannot evaluate rules. "
            "Refusing to proceed — an unevaluatable policy must never read as ALLOW.")
    env = celpy.Environment()
    program = env.program(env.compile(expr))
    result = program.evaluate(celpy.json_to_cel(context))
    return bool(result)


def evaluate(context: dict, rules: list[dict] | None = None) -> Decision:
    """context carries: experiment, target, load, blast, budget.

    Order is fixed by the policy contract: every DENY is considered before any
    REQUIRE_OVERRIDE, so a rule's position in the file cannot change the outcome.
    """
    rules = rules if rules is not None else load_rules()

    for wanted in ("deny", "require_override"):
        for rule in rules:
            if rule.get("effect") != wanted:
                continue
            try:
                matched = _evaluate_expr(rule["expr"], context)
            except Exception as e:
                # Fail closed: an expression we cannot evaluate denies the run.
                return Decision("deny", rule.get("id"),
                                f"policy rule {rule.get('id')!r} could not be "
                                f"evaluated ({e}); failing closed")
            if matched:
                return Decision(wanted, rule["id"],
                                " ".join(str(rule.get("message", "")).split()))

    return Decision("allow")
