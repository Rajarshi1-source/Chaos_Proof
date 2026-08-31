"""The CI contract diff (§19.3).

    python -m src.contracts.diff --base main

A PR that WEAKENS a contract — raising `max_error_rate_pct`, dropping a
degradation mode, deleting a clause — surfaces a diff to the people who relied
on the old guarantee. **It does not auto-block.**

That restraint is the design, not a compromise. A legitimate weakening should
be VISIBLE to its consumers, not forbidden: a service that genuinely can only
absorb 15% now needs to say so, and a gate that refuses the change just gets
bypassed with an admin merge — after which nobody is having the conversation
either. Tooling that forces a conversation beats tooling that forces a merge
failure.

So this exits 0 even when it finds a weakening. It writes a PR comment, names
the owners, and says what the old promise was.
"""

import argparse
import os
import pathlib
import subprocess
import sys

import yaml

from .model import Contract, ContractError, parse

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CONTRACT_GLOB = "target-app/*/resilience-contract.yaml"

# For each numeric clause, the direction that WEAKENS the promise.
#   "up"   raising it weakens (tolerating more is a weaker claim about you...
#          no: tolerating more is a STRONGER claim. See the note below.)
# The distinction that matters is who relies on the number, and it is the
# opposite of the intuition for `tolerates`:
#
#   provides.availability_slo   consumers rely on it. LOWERING weakens.
#   provides.latency_p99_ms     consumers rely on it. RAISING weakens.
#   tolerates.*.max_*           the DEPENDENCY relies on it: "you may fail this
#                               much and I will cope". RAISING it sounds
#                               stronger but is published as a tested guarantee,
#                               and raising it AFTER a violation is how a
#                               failing clause gets made to pass. Both
#                               directions are surfaced; raising is flagged as
#                               "weakens what was verified" because the old,
#                               tighter number is what consumers were told.
WEAKENS_WHEN = {
    "provides.availability_slo": "down",
    "provides.latency_p99_ms": "up",
}


def _git_show(ref: str, path: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)
    return proc.stdout if proc.returncode == 0 else None


def _load_ref(ref: str, path: str) -> Contract | None:
    raw = _git_show(ref, path)
    if raw is None:
        return None
    try:
        return parse(yaml.safe_load(raw), path)
    except (ContractError, KeyError, TypeError, yaml.YAMLError):
        return None


def _numeric_clauses(c: Contract) -> dict[str, float]:
    out = {"provides.availability_slo": float(c.provides.availability_slo),
           "provides.latency_p99_ms": float(c.provides.latency_p99_ms)}
    for tol in c.tolerates:
        for clause_id, _dim, value in tol.clauses():
            out[clause_id] = value
    for d in c.does_not_inflict:
        if d.threshold is not None:
            out[d.clause_id] = d.threshold
    return out


def compare(before: Contract | None, after: Contract) -> list[dict]:
    """Every material change, each labelled with whether it weakens a promise."""
    if before is None:
        return [{"kind": "added", "clause": "(whole contract)", "weakens": False,
                 "detail": f"new contract for {after.service} v{after.version}"}]

    changes: list[dict] = []
    old_n, new_n = _numeric_clauses(before), _numeric_clauses(after)

    for clause in sorted(set(old_n) | set(new_n)):
        old, new = old_n.get(clause), new_n.get(clause)
        if old is None:
            changes.append({"kind": "added", "clause": clause, "weakens": False,
                            "detail": f"{new:g}"})
        elif new is None:
            changes.append({"kind": "removed", "clause": clause, "weakens": True,
                            "detail": f"was {old:g}; the guarantee is gone",
                            "old": old})
        elif old != new:
            direction = "up" if new > old else "down"
            if clause in WEAKENS_WHEN:
                weakens = direction == WEAKENS_WHEN[clause]
                why = ("consumers relied on this figure" if weakens else "")
            else:
                # tolerates.* and does_not_inflict.*: raising the number is the
                # move that makes a previously-violated clause pass.
                weakens = direction == "up"
                why = ("the previous, tighter figure is what consumers were "
                       "told, and raising it after a violation makes a failing "
                       "clause pass rather than fixing it" if weakens else "")
            changes.append({"kind": "changed", "clause": clause, "weakens": weakens,
                            "detail": f"{old:g} -> {new:g}", "old": old, "new": new,
                            "why": why})

    old_modes = set(before.provides.graceful_degradation_modes)
    new_modes = set(after.provides.graceful_degradation_modes)
    for gone in sorted(old_modes - new_modes):
        changes.append({"kind": "removed", "clause": "provides.graceful_degradation_modes",
                        "weakens": True,
                        "detail": f"dropped degradation mode: {gone!r}"})
    for added in sorted(new_modes - old_modes):
        changes.append({"kind": "added", "clause": "provides.graceful_degradation_modes",
                        "weakens": False, "detail": f"new degradation mode: {added!r}"})

    return changes


def render(service: str, before: Contract | None, after: Contract,
           changes: list[dict]) -> str:
    old_v = before.version if before else "(new)"
    lines = [f"CONTRACT DIFF  {service} {old_v} -> {after.version}"]
    if not changes:
        lines.append("  no material change to any published guarantee")
        return "\n".join(lines)

    for ch in changes:
        marker = {"added": "+", "removed": "-", "changed": "~"}[ch["kind"]]
        lines.append(f"  {marker} {ch['clause']}: {ch['detail']}")
        if ch["weakens"]:
            owners = " ".join(after.owners) or "(no owners declared)"
            lines.append(f"    WARNING  This weakens a published guarantee. "
                         f"Reviewers: {owners}")
            if ch.get("why"):
                lines.append(f"             {ch['why']}")
            lines.append("             This is NOT blocked. A legitimate "
                         "weakening should be visible to the people who relied "
                         "on it, not forbidden. Is the change intentional?")
    return "\n".join(lines)


def _post_pr_comment(body: str) -> bool:
    """Best effort. No token, no comment, no failure — the diff is still in the
    job log, and a missing credential must not look like a contract problem."""
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        return False
    pr = os.environ.get("PR_NUMBER") or ""
    if not pr:
        return False
    proc = subprocess.run(["gh", "pr", "comment", pr, "--body", body],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="contracts.diff")
    ap.add_argument("--base", default="main",
                    help="git ref to compare against (the PR's base branch)")
    ap.add_argument("--comment", action="store_true",
                    help="post the diff as a PR comment when a weakening is found")
    args = ap.parse_args(argv)

    base = args.base if args.base else "main"
    # In CI the base branch may not be fetched; say so rather than reporting
    # "no contracts changed", which is the same output as success.
    probe = subprocess.run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
                           cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    if probe.returncode != 0:
        for candidate in (f"origin/{base}", f"refs/remotes/origin/{base}"):
            probe = subprocess.run(
                ["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
            if probe.returncode == 0:
                base = candidate
                break
        else:
            print(f"::warning title=contract-gate::base ref {args.base!r} is not "
                  "available in this checkout (shallow clone?), so no contract "
                  "diff was computed. This is NOT a clean result.")
            return 0

    weakenings = 0
    reports: list[str] = []
    for path in sorted(REPO_ROOT.glob(CONTRACT_GLOB)):
        rel = path.relative_to(REPO_ROOT).as_posix()
        after = parse(yaml.safe_load(path.read_text(encoding="utf-8")), rel)
        before = _load_ref(base, rel)
        changes = compare(before, after)
        material = [c for c in changes if c["kind"] != "added" or c["weakens"]]
        if not changes or (before is not None and not material and
                           not any(c["kind"] == "added" for c in changes)):
            continue
        text = render(after.service, before, after, changes)
        reports.append(text)
        print(text)
        print()
        weakenings += sum(1 for c in changes if c["weakens"])

    if not reports:
        print(f"CONTRACT DIFF: no contract changed against {base}.")
        return 0

    if weakenings:
        print(f"CONTRACT DIFF: {weakenings} weakened guarantee(s) surfaced for "
              f"review. NOT BLOCKING — tooling that forces a conversation beats "
              f"tooling that forces a merge failure.")
        if args.comment:
            body = "## Resilience contract diff\n\n```\n" + \
                   "\n\n".join(reports) + "\n```\n"
            if _post_pr_comment(body):
                print("posted as a PR comment")
    else:
        print("CONTRACT DIFF: contracts changed, no published guarantee weakened.")

    # Always 0. See the module docstring.
    return 0


if __name__ == "__main__":
    sys.exit(main())
