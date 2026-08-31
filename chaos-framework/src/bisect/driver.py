"""The live candidate evaluator - the one part of bisection that needs a cluster.

`runner.bisect()` is pure and takes a callable. This is the callable that makes
it real: for each candidate commit it builds and deploys the TARGET APPLICATION
at that commit, runs the experiment `reps` times, and returns one score per
repetition.

Two decisions here are load-bearing and neither is obvious.

**A worktree, not a checkout.** `git checkout <candidate>` would roll the whole
repository back - including `chaos-framework/`, which holds the scorer, the
weights, the SLO version and the hypotheses. Every candidate would then be
scored by whatever scorer existed at that commit, and the binary search would
happily converge on the commit that edited the weights. `git worktree add`
materialises only the target-app tree at the candidate while the framework
executing the search stays exactly where it is. `commits.epoch_conflicts()`
refuses the range outright if it touches an epoch-defining path, so the two
mechanisms cover each other: the worktree keeps the scorer pinned during the
search, and the pre-flight check catches a range where pinning would not be
enough because the endpoints were never comparable.

**A repetition that produces no score returns None, not zero.** INVALID means
the measurement plane failed; ABORTED means the safety plane cut the run short.
Neither says anything about the resilience of the code at that commit. Averaging
a zero in would drag the candidate toward the bad anchor and pick a half on the
strength of a run that measured nothing - the exact failure the INVALID verdict
exists to prevent, arriving through a side door.

STATUS: this module has NOT been executed against a live cluster. Bisection
needs one cluster, exclusively, for roughly ninety minutes with a rebuild and
redeploy of the target application between candidates, which the single kind
node this project runs on cannot provide while the daily schedule also uses it.
The search logic, the sigma arithmetic, the cost model and every refusal path
are covered by tests that need no cluster; the shell-outs below are not. That
distinction is stated here rather than left for someone to discover.
"""

import pathlib
import subprocess
import tempfile

# Steps to stand up the target application at a candidate commit. Kept as data
# so the sequence is readable and so a caller can substitute it wholesale in a
# different environment rather than editing control flow.
BUILD_STEPS = (
    ("build", ["make", "target-app"]),
)

DEFAULT_SETTLE_S = 30.0


class CandidateBuildFailed(RuntimeError):
    """The candidate would not build or deploy.

    NOT the same as a bad score, and deliberately not converted into one: a
    commit that does not build tells you nothing about its resilience, and
    scoring it zero would make "broken build" indistinguishable from "removed
    the circuit breaker" to the binary search.
    """


def _run(cmd: list[str], cwd: str | pathlib.Path, timeout: float) -> str:
    out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                         timeout=timeout)
    if out.returncode != 0:
        raise CandidateBuildFailed(
            f"{' '.join(cmd)} exited {out.returncode}: {out.stderr.strip()[:400]}")
    return out.stdout


class WorktreeDriver:
    """Evaluates candidates by deploying the target app from a detached worktree.

    Usage:
        driver = WorktreeDriver(repo=REPO_ROOT, experiment="pod_kill_payment_svc")
        result = bisect(..., evaluate=driver)
        driver.cleanup()

    `cleanup()` removes the worktree. Like the cleanup saga, it must run on every
    exit path - a leftover worktree is a stale copy of the repository that the
    next bisection would find and reuse.
    """

    def __init__(self, repo: pathlib.Path, experiment: str, *,
                 build_timeout_s: float = 900.0,
                 run_timeout_s: float = 900.0,
                 settle_s: float = DEFAULT_SETTLE_S,
                 log=print):
        self.repo = pathlib.Path(repo)
        self.experiment = experiment
        self.build_timeout_s = build_timeout_s
        self.run_timeout_s = run_timeout_s
        self.settle_s = settle_s
        self.log = log
        self._worktree: pathlib.Path | None = None

    # -- lifecycle ---------------------------------------------------------- #

    def _ensure_worktree(self) -> pathlib.Path:
        if self._worktree is None:
            path = pathlib.Path(tempfile.mkdtemp(prefix="chaosproof-bisect-"))
            # --detach: no branch is created, so nothing here can be pushed by
            # accident and no branch name collides with a real one.
            _run(["git", "worktree", "add", "--detach", str(path), "HEAD"],
                 self.repo, 120)
            self._worktree = path
        return self._worktree

    def cleanup(self) -> None:
        if self._worktree is None:
            return
        try:
            _run(["git", "worktree", "remove", "--force", str(self._worktree)],
                 self.repo, 120)
        except (CandidateBuildFailed, OSError, subprocess.TimeoutExpired) as e:
            self.log(f"worktree cleanup failed, remove {self._worktree} by hand: {e}")
        finally:
            self._worktree = None

    # -- the callable runner.bisect() consumes ------------------------------ #

    def __call__(self, sha: str, reps: int) -> list[float | None]:
        tree = self._ensure_worktree()
        self.log(f"  checkout {sha[:12]} into worktree")
        _run(["git", "checkout", "--detach", sha], tree, 120)

        for name, cmd in BUILD_STEPS:
            self.log(f"  {name}: {' '.join(cmd)}")
            _run(list(cmd), tree, self.build_timeout_s)

        scores: list[float | None] = []
        for i in range(1, reps + 1):
            self.log(f"  rep {i}/{reps} at {sha[:12]}")
            scores.append(self._one_run())
        return scores

    def _one_run(self) -> float | None:
        """One execution, scored, from the framework at HEAD.

        Deliberately invoked through the same orchestrator entry point as every
        other run: a bisection that scored candidates by a private code path
        would be measuring something other than what the daily schedule
        measures, and the anchors it compares against came from the daily
        schedule.
        """
        from ..orchestrator import runner as orch

        spec_path = self._spec_path()
        try:
            outcome = orch.run(spec_path, prom_url=self._prom_url(),
                               testrun=self._testrun(),
                               alertmanager_url=self._alertmanager_url())
        except orch.PreflightSkip as skip:
            # SKIPPED is not a zero. Pre-flight refusing to inject says the
            # cluster was not in a state to be tested, not that the code is bad.
            self.log(f"    pre-flight skipped this repetition: {skip}")
            return None

        # `orch.run` returns a dict whose "score" is a ScoreResult, whose own
        # `.score` is None for an unscoreable run. Both levels of None mean the
        # same thing here and both stay None.
        result = outcome.get("score")
        value = getattr(result, "score", None)
        if value is None:
            self.log(f"    no score (verdict {outcome.get('verdict')}) - counted "
                     f"as unmeasured, not as zero")
        return value

    # -- environment, isolated so tests can override ------------------------ #

    def _spec_path(self) -> pathlib.Path:
        from ..chaosctl import _resolve
        return _resolve(self.experiment)

    @staticmethod
    def _prom_url() -> str:
        import os
        return os.environ.get("PROM_URL", "http://localhost:19090")

    @staticmethod
    def _testrun() -> str:
        import os
        return os.environ.get("TESTRUN", "steady-120rps")

    @staticmethod
    def _alertmanager_url() -> str:
        import os
        return os.environ.get("ALERTMANAGER_URL", "http://localhost:19093")
