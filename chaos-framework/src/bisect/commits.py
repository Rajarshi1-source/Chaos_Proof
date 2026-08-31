"""Resolving the commit range - and the check that stops a bisection from
bisecting its own scorer.

This is the trap that is easy to walk into. Bisection compares a score at commit
A against a score at commit B. But the scorer, the weights, the SLO definitions
and the hypotheses all live in the same repository as the target application. If
`git checkout <candidate>` rolls the whole tree back, then a candidate is scored
by whatever scorer existed at that commit - and a range that happens to contain
a weight change produces a score difference that has nothing to do with
resilience. The binary search would then converge, confidently, on the commit
that edited the scorer.

Scoring epochs exist precisely to stop two incomparable numbers being compared.
So the same rule applies here, in the strongest form:

  1. Only the TARGET APPLICATION tree is rolled back to each candidate. The
     framework - scorer, constants, hypotheses, SLOs - stays pinned at the
     revision the bisection was launched from, so every candidate is scored the
     same way.
  2. If the range itself touches any epoch-defining path, the bisection is
     REFUSED rather than run with a caveat. Pinning the framework does not
     rescue that case: the range contains a deliberate change to what a score
     means, so the endpoints were never comparable to begin with, and the
     correct answer is that the score moved because someone changed the scorer.

That second case is a finding, not an obstacle. It is the same finding the epoch
boundary on the trend chart reports, arriving through a different door.
"""

import subprocess
from dataclasses import dataclass

# Paths whose contents enter the epoch material, or determine it:
#   weights and thresholds -> constants.py
#   scorer + SLO version   -> scoring/
#   hypothesis versions and the gating experiment set -> experiments/, slos/
# A change under any of these between `good` and `bad` means the two scores were
# computed under different definitions.
EPOCH_DEFINING_PATHS = (
    "chaos-framework/src/constants.py",
    "chaos-framework/src/scoring/",
    "chaos-framework/src/hypothesis/",
    "experiments/",
    "slos/",
)


class GitUnavailable(RuntimeError):
    """Raised when the repository cannot answer. Never swallowed: a bisection
    that silently guesses a commit range is worse than one that does not run."""


@dataclass(frozen=True)
class CommitRange:
    good: str                       # oldest, known-good
    bad: str                        # newest, known-bad
    shas: list[str]                 # oldest -> newest, INCLUSIVE of both ends
    epoch_conflicts: list[str]      # epoch-defining paths changed in the range

    @property
    def width(self) -> int:
        """Commits the search must discriminate between: the number of steps
        from good to bad. `shas` includes both ends, so this is len - 1."""
        return len(self.shas) - 1

    @property
    def epoch_safe(self) -> bool:
        return not self.epoch_conflicts


def _git(args: list[str], repo: str) -> str:
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitUnavailable(f"git {' '.join(args)} failed: {e}") from e
    if out.returncode != 0:
        raise GitUnavailable(f"git {' '.join(args)} exited {out.returncode}: "
                             f"{out.stderr.strip()}")
    return out.stdout


def resolve_range(good: str, bad: str, repo: str = ".") -> CommitRange:
    """Commits from `good` to `bad`, oldest first, with the epoch check applied.

    `good..bad` in git means "reachable from bad but not from good", so it
    EXCLUDES good. The good anchor is prepended because the search treats it as
    index 0 - the last commit known to carry the good score.
    """
    good_full = _git(["rev-parse", good], repo).strip()
    bad_full = _git(["rev-parse", bad], repo).strip()

    if good_full == bad_full:
        raise GitUnavailable(
            f"good and bad resolve to the same commit {good_full[:12]} - "
            f"there is nothing between them to search")

    # --first-parent keeps the search on the mainline. Without it, a merge
    # commit drags a topic branch's commits into the range, and checking one out
    # produces a tree that never existed on the branch whose score regressed.
    raw = _git(["rev-list", "--first-parent", "--reverse",
                f"{good_full}..{bad_full}"], repo)
    later = [line.strip() for line in raw.splitlines() if line.strip()]
    if not later:
        raise GitUnavailable(
            f"{bad[:12]} is not a descendant of {good[:12]} on the first-parent "
            f"line - check the argument order, or bisect from their merge base")

    conflicts = epoch_conflicts(good_full, bad_full, repo)
    return CommitRange(good=good_full, bad=bad_full, shas=[good_full, *later],
                       epoch_conflicts=conflicts)


def epoch_conflicts(good: str, bad: str, repo: str = ".") -> list[str]:
    """Epoch-defining paths touched between the two anchors.

    Non-empty means the endpoints were scored under different definitions, and
    the score difference is at least partly an artefact of that change.
    """
    raw = _git(["diff", "--name-only", f"{good}..{bad}"], repo)
    changed = [line.strip() for line in raw.splitlines() if line.strip()]
    hits = []
    for path in changed:
        for guard in EPOCH_DEFINING_PATHS:
            if path == guard or path.startswith(guard):
                hits.append(path)
                break
    return sorted(set(hits))


def subject(sha: str, repo: str = ".") -> str:
    """One-line subject, for a result a human has to read."""
    try:
        return _git(["log", "-1", "--format=%s", sha], repo).strip()
    except GitUnavailable:
        return ""
