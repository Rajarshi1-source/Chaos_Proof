"""The quarantine transition (§21.3) — measure, demote, announce, open an epoch.

Quarantine is NOT deletion. A quarantined experiment keeps running and keeps
reporting; it simply loses the authority to block a merge. Deleting it would
destroy the evidence needed to fix it and quietly shrink the suite, which is
how a chaos programme decays into the two experiments nobody has broken yet.

The four effects of a demotion, in order, and why the order matters:

  1. RECORD the measured status. If this fails, nothing else should happen —
     an announcement about a state that was never persisted is a lie.
  2. OPEN AN EPOCH, if the gating bit moved. Only gating experiments are in the
     epoch's experiment_set, so a demotion changes what the composite score is
     an average OF. Skipping this would let the trend line span the boundary,
     which is the exact defect epochs exist to prevent.
  3. FILE AN ISSUE against the EXPERIMENT — not against the service. A flaky
     experiment is a bug in the experiment until proven otherwise.
  4. NOTIFY Slack, naming sigma.

THE SPLIT BETWEEN 1-2 AND 3-4 IS A TRANSACTION BOUNDARY, and it was learned
the hard way. The first version did all four inside one transaction. An
ambiguous-column error in a cosmetic back-link UPDATE then aborted the
transaction AFTER the GitHub issue had been created — leaving a filed issue
announcing a demotion that had just been rolled back. The evidence store said
one thing and GitHub said another, which is precisely the state rule 1 is
written to prevent.

So `apply()` touches only the database, the caller COMMITS, and `announce()`
runs afterwards. Announcements are best-effort in the other direction too: an
unreachable Slack webhook or a missing GitHub credential must never fail a
transition that already happened. The demotion is the fact; the announcements
are convenience.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field

from ..integrations import slack
from . import epochs, flakiness
from .flakiness import FlakinessVerdict

ISSUE_LABEL = "flaky-experiment"


@dataclass
class Transition:
    experiment: str
    verdict: FlakinessVerdict
    gating_changed: bool
    epoch_opened: str | None = None
    issue_url: str | None = None
    notified: bool = False
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"{self.experiment}: {self.verdict.status}",
                f"gating={self.verdict.gating}"]
        if self.gating_changed:
            bits.append("GATING CHANGED")
        if self.epoch_opened:
            bits.append(f"epoch {self.epoch_opened[:12]}")
        if self.issue_url:
            bits.append(f"issue {self.issue_url}")
        return "  ".join(bits)


def apply(cur, experiment: str, verdict: FlakinessVerdict) -> Transition:
    """DATABASE ONLY. Record the verdict, open an epoch if the gating bit moved,
    and append the transition. The caller commits; `announce()` runs after."""
    gating_changed = flakiness.record(cur, experiment, verdict)
    t = Transition(experiment, verdict, gating_changed)

    if gating_changed:
        # The gating set is an epoch input. Moving it MUST open an epoch, and
        # the reason is written so the boundary on the trend chart explains
        # itself rather than saying "scorer changed".
        direction = "promoted to gating" if verdict.gating else "quarantined to advisory"
        reason = f"{experiment} {direction} — {verdict.reason}"
        try:
            epoch = epochs.open_epoch(cur, reason)
            t.epoch_opened = epoch.sha256
        except Exception as exc:                                   # noqa: BLE001
            # An epoch that could not be opened is a correctness problem, not a
            # cosmetic one: scores after this point would be stamped with an
            # epoch that no longer describes them. Surface it loudly.
            t.errors.append(f"FAILED to open epoch: {type(exc).__name__}: {exc}")

    # Append-only history. experiment_flakiness holds the LATEST measurement and
    # is therefore mutable, which leaves it unable to answer "when did this lose
    # gating status, and what was sigma then?" — the first question anyone asks
    # when a trend line breaks.
    _record_transition(cur, experiment, verdict, t)
    return t


def announce(t: Transition, *, file_issue: bool = True,
             notify: bool = True) -> Transition:
    """AFTER COMMIT. Best-effort announcements for a demotion that has already
    been persisted. Never raises: a transition that happened must not be
    reported as failed because Slack was unreachable."""
    if t.verdict.status != "flaky":
        return t

    if file_issue:
        try:
            t.issue_url = open_github_issue(t.experiment, t.verdict)
        except Exception as exc:                                   # noqa: BLE001
            t.errors.append(f"issue not filed: {type(exc).__name__}: {exc}")
    if notify:
        try:
            t.notified = post_slack(t.experiment, t.verdict, t.epoch_opened)
        except Exception as exc:                                   # noqa: BLE001
            t.errors.append(f"slack notice not sent: {type(exc).__name__}: {exc}")
    return t


def link_issue(cur, t: Transition) -> None:
    """Back-link the filed issue onto the recorded transition. Runs in its own
    transaction, after the demotion is already durable, so a failure here can
    only cost a hyperlink — never the demotion itself."""
    if not t.issue_url:
        return
    # max(f.id), not max(id): `id` is ambiguous across the join.
    cur.execute("""UPDATE flakiness_transitions SET issue_url = %s
                    WHERE id = (SELECT max(f.id)
                                  FROM flakiness_transitions f
                                  JOIN experiment_types et
                                    ON et.id = f.experiment_type_id
                                 WHERE et.name = %s)""",
                (t.issue_url, t.experiment))


def _record_transition(cur, experiment: str, verdict: FlakinessVerdict,
                       t: Transition) -> None:
    cur.execute("SELECT id FROM experiment_types WHERE name = %s", (experiment,))
    row = cur.fetchone()
    if row is None:
        return
    type_id = row[0]

    epoch_id = None
    if t.epoch_opened:
        cur.execute("SELECT id FROM scoring_epochs WHERE epoch_sha256 = %s",
                    (t.epoch_opened,))
        found = cur.fetchone()
        epoch_id = found[0] if found else None
        if epoch_id is not None:
            cur.execute("UPDATE scoring_epochs SET opened_by = %s "
                        "WHERE id = %s AND opened_by IS NULL", (experiment, epoch_id))

    cur.execute(
        """INSERT INTO flakiness_transitions
               (experiment_type_id, from_gating, to_gating, status, window_runs,
                score_stddev, verdict_flip_rate, reason, opened_epoch_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (type_id, (not verdict.gating) if t.gating_changed else verdict.gating,
         verdict.gating, verdict.status, verdict.window_runs,
         verdict.sigma, verdict.flip_rate, verdict.reason, epoch_id))


# --------------------------------------------------------------------------- #
# The GitHub issue — filed against the experiment.
# --------------------------------------------------------------------------- #

def issue_body(experiment: str, verdict: FlakinessVerdict) -> str:
    ev = verdict.evidence or {}
    scores = ev.get("scores") or []
    verdicts = ev.get("verdicts") or []
    lines = [
        f"`{experiment}` was automatically demoted to **advisory** by flakiness "
        "measurement. It has NOT been disabled: it keeps running and reporting, "
        "it simply cannot block a merge until it is stable again.",
        "",
        "## Measurement",
        "",
        f"- window: **{verdict.window_runs}** scoreable runs on unchanged `git_sha` "
        "and unchanged `hypothesis.version`",
        f"- score sigma: **{verdict.sigma:.4f}** (max {ev.get('max_sigma')})"
        if verdict.sigma is not None else "- score sigma: n/a",
        f"- verdict flip rate: **{verdict.flip_rate:.1%}** (max "
        f"{float(ev.get('max_flip_rate', 0)):.1%})"
        if verdict.flip_rate is not None else "- verdict flip rate: n/a",
        "",
        f"> {verdict.reason}",
        "",
    ]
    if scores:
        lines += ["## Scores in the window", "",
                  "```", " ".join(f"{s:.4f}" for s in scores), "```", ""]
    if verdicts:
        lines += ["## Verdicts in the window", "",
                  "```", " ".join(verdicts), "```", ""]
    lines += [
        "## What to do",
        "",
        "This is a bug in the **experiment** until proven otherwise — variance on "
        "unchanged code means the experiment is measuring something other than the "
        "system. Usual causes, in the order worth checking:",
        "",
        "1. A tolerance or recovery deadline set too close to the observed value, so "
        "   ordinary jitter crosses it.",
        "2. An invariant reading a signal with its own noise floor (a rate window "
        "   shorter than the metric's update interval, say).",
        "3. A fault whose actual blast radius varies between runs — a percentage-based "
        "   target selection that sometimes hits the pod serving traffic and sometimes "
        "   does not.",
        "4. Genuine bistability in the system under test. This is the interesting case, "
        "   and it is a finding rather than a defect in the experiment.",
        "",
        "**Do not fix this by widening the tolerance until it passes.** A tolerance "
        "chosen to make an experiment stable, rather than from what the system should "
        "do, produces an experiment that cannot fail — which is worse than a flaky one, "
        "because it looks healthy.",
        "",
        "Bumping `hypothesis.version` resets this experiment to advisory and clears its "
        "flakiness history, which is correct when the assertion itself changes.",
        "",
        "---",
        "*Filed automatically by ChaosProof flakiness quarantine (§21.3).*",
    ]
    return "\n".join(lines)


def _gh_available() -> bool:
    """Is there a usable GitHub credential?

    Two sources, because the two environments differ: CI exports GITHUB_TOKEN,
    while a developer's machine usually has `gh` logged in through a keyring
    with no token in the environment at all. Checking only the env var made
    this a silent no-op locally — the quarantine would report success having
    filed nothing.
    """
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        return True
    try:
        return subprocess.run(["gh", "auth", "status"], capture_output=True,
                              text=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def open_github_issue(experiment: str, verdict: FlakinessVerdict) -> str | None:
    """Best-effort `gh issue create`. Returns the URL, or None when no GitHub
    credential is available — a missing credential is a no-op, never an error,
    so a laptop run behaves the same as CI minus the announcement."""
    if not _gh_available():
        return None

    title = f"Flaky experiment: {experiment} demoted to advisory"
    existing = _find_open_issue(title)
    if existing:
        # Re-filing every night turns a signal into noise and trains people to
        # filter the label.
        return existing

    proc = subprocess.run(
        ["gh", "issue", "create", "--title", title,
         "--body", issue_body(experiment, verdict), "--label", ISSUE_LABEL],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gh issue create failed")
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None


def _find_open_issue(title: str) -> str | None:
    proc = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--label", ISSUE_LABEL,
         "--json", "title,url"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return None
    try:
        for issue in json.loads(proc.stdout or "[]"):
            if issue.get("title") == title:
                return issue.get("url")
    except json.JSONDecodeError:
        return None
    return None


# --------------------------------------------------------------------------- #
# The Slack notice — names sigma, because a demotion without a number is a rumour.
# --------------------------------------------------------------------------- #

def quarantine_blocks(experiment: str, verdict: FlakinessVerdict,
                      epoch_sha: str | None) -> list[dict]:
    fields = [
        {"type": "mrkdwn", "text": f"*Experiment*\n`{experiment}`"},
        {"type": "mrkdwn", "text": f"*Window*\n{verdict.window_runs} runs, unchanged SHA"},
    ]
    if verdict.sigma is not None:
        fields.append({"type": "mrkdwn",
                       "text": f"*Score sigma*\n`{verdict.sigma:.4f}` "
                               f"(max `{verdict.evidence.get('max_sigma')}`)"})
    if verdict.flip_rate is not None:
        fields.append({"type": "mrkdwn",
                       "text": f"*Verdict flips*\n`{verdict.flip_rate:.1%}`"})
    if epoch_sha:
        fields.append({"type": "mrkdwn", "text": f"*New epoch*\n`{epoch_sha[:12]}`"})

    return [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"Quarantined to advisory: {experiment}"}},
        {"type": "section", "fields": fields},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"{verdict.reason}\n\n"
                          "*It has not been disabled* — it keeps running and reporting, "
                          "it just cannot block a merge until it is stable again."}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": "The gating set changed, so this demotion opened a new "
                               "scoring epoch: the composite score is now an average "
                               "over a different set of experiments, and the trend line "
                               "breaks here rather than spanning it."
                               if epoch_sha else
                               "Gating status unchanged; no new epoch."}]},
    ]


def post_slack(experiment: str, verdict: FlakinessVerdict,
               epoch_sha: str | None) -> bool:
    return slack.post(quarantine_blocks(experiment, verdict, epoch_sha))
