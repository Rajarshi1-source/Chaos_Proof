"""Policy eval — the CI gate on policy/chaos_safety.yaml.

Every rule must carry a must_deny case (evidence the guardrail fires) and a
must_allow case (evidence it does not fire on innocent input). A rule with only
the first is untested; a rule with only the second is decorative.

The policy file is code. It gets tested like code.

Run:  python -m evals.policy_eval
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "chaos-framework"))

from src.safety import policy                            # noqa: E402

# A context every rule can evaluate against. Cases override only what they mean
# to exercise, so a rule cannot accidentally pass because an unrelated key was
# missing and its expression silently errored.
BASE = {
    "experiment": {"target_namespace": "target-app", "target_kind": "Deployment",
                   "target_name": "payment-service", "mode": "standard",
                   "min_rps_floor": 90.0},
    "target": {"replicas": 2},
    "load": {"current_rps": 120.0},
    "blast": {"score": 34, "error_budget_burn_pct": 0.014},
    "budget": {"gate_state": "open"},
}


def merged(overrides: dict) -> dict:
    ctx = {k: dict(v) for k, v in BASE.items()}
    for section, values in (overrides or {}).items():
        ctx.setdefault(section, {}).update(values)
    return ctx


def main() -> int:
    rules = policy.load_rules()
    failures: list[str] = []
    print(f"{'RULE':42} {'EFFECT':17} MUST_DENY  MUST_ALLOW")

    for rule in rules:
        rid, effect = rule["id"], rule["effect"]

        if "must_deny" not in rule or "must_allow" not in rule:
            failures.append(f"{rid}: missing must_deny and/or must_allow case — "
                            "an untested guardrail is a guardrail you are guessing about")
            continue

        # must_deny: THIS rule must be the one that fires.
        d = policy.evaluate(merged(rule["must_deny"]), rules)
        deny_ok = d.effect == effect and d.rule_id == rid

        # must_allow: nothing may fire on innocent input.
        a = policy.evaluate(merged(rule["must_allow"]), rules)
        allow_ok = a.effect == "allow"

        if not deny_ok:
            failures.append(f"{rid}: must_deny case produced {d.effect}"
                            f"/{d.rule_id}, expected {effect}/{rid}")
        if not allow_ok:
            failures.append(f"{rid}: must_allow case was blocked by "
                            f"{a.rule_id} ({a.effect})")

        print(f"{rid:42} {effect:17} {'ok' if deny_ok else 'FAIL':9}  "
              f"{'ok' if allow_ok else 'FAIL'}")

    # The rule that must exist, whatever else changes.
    if not any(r["id"] == "deny-chaosproof-self" for r in rules):
        failures.append("deny-chaosproof-self is missing: ChaosProof must refuse to "
                        "inject chaos into its own namespace")

    # Fail-closed check: a rule whose expression cannot be evaluated must DENY,
    # never silently allow.
    broken = [{"id": "broken", "effect": "deny", "expr": "this is not valid CEL ((("}]
    if policy.evaluate(BASE, broken).effect != "deny":
        failures.append("an unevaluatable rule must fail CLOSED (deny), not allow")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print(f"POLICY EVAL: {len(rules)} rules, each with a must-deny and a must-allow "
          "case; unevaluatable rules fail closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
