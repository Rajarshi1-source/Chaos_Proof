"""Slack Block Kit reporting.

Two rules this module holds to, both inherited from the dashboard's contract
because a Slack message is just the dashboard with a smaller viewport:

  - **A verdict is never posted without naming what produced it.** "The
    experiment failed" is not actionable; "client_p99_holds breached 800ms for
    34s" is.
  - **SKIPPED, DENIED and ABORTED are never styled as failures.** A red
    "denied" post would contradict the project's thesis, which is that refusing
    to act and refusing to answer are correct behaviours. Slack has no colour
    tokens, so the emoji does that job — and :no_entry: is reserved for a real
    framework error.

Posting is OPTIONAL by design: with no webhook configured the reporter renders
and returns the blocks without sending. A portfolio project that breaks in a
live demo because an integration is unconfigured is a liability.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

WEBHOOK_ENV = "CHAOSPROOF_SLACK_WEBHOOK"

# The emoji carries the semantics Slack has no tokens for. Note that `invalid`
# is neither the pass nor the fail marker, and that refusals are neutral.
VERDICT_MARKER = {
    "held":      (":white_check_mark:", "HYPOTHESIS_HELD"),
    "falsified": (":warning:", "HYPOTHESIS_FALSIFIED"),
    "invalid":   (":grey_question:", "INVALID"),      # could not measure
    "skipped":   (":pause_button:", "SKIPPED"),       # guardrail fired
    "denied":    (":lock:", "DENIED"),                # policy refused
    "aborted":   (":octagonal_sign:", "ABORTED"),     # safety plane worked
    "error":     (":no_entry:", "ERROR"),             # the only red
}


def grafana_link(base: str, experiment: str, from_ms: int, to_ms: int) -> str:
    """Deep link scoped to the experiment's OWN time range, with padding either
    side. A link to 'now' is useless by the time anyone clicks it."""
    pad = 120_000
    return (f"{base.rstrip('/')}/d/chaosproof-experiment/chaosproof-experiment"
            f"?from={from_ms - pad}&to={to_ms + pad}&var-experiment={experiment}")


def build_blocks(*, experiment: str, verdict: str, reason: str | None,
                 execution_id: int, score: float | None,
                 weights_denominator: float | None, epoch: str | None,
                 invariants: list[dict], load: dict | None,
                 grafana_url: str | None) -> list[dict]:
    marker, label = VERDICT_MARKER.get(verdict, (":grey_question:", verdict.upper()))

    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"{marker} {experiment} — {label}"},
    }]

    if reason:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"_{reason}_"}})

    # A score is NEVER shown without its epoch: a bare number invites exactly the
    # comparison that scoring epochs exist to prevent.
    if score is not None:
        score_text = (f"*Score* `{score:.4f}`  "
                      f"(denominator `{weights_denominator:.3f}`)" if weights_denominator
                      else f"*Score* `{score:.4f}`")
        score_text += f"\n*Epoch* `{(epoch or 'unknown')[:12]}`"
    else:
        score_text = ("*Score* — _not scoreable_\n"
                      "An unmeasured or halted run carries no score, not a zero.")

    fields = [{"type": "mrkdwn", "text": score_text},
              {"type": "mrkdwn", "text": f"*Execution* `#{execution_id}`"}]
    if load:
        fields.append({"type": "mrkdwn",
                       "text": (f"*Load* {load.get('achievedRps', 0):.0f} rps "
                                f"· dropped {load.get('droppedIterations', 0)}")})
    blocks.append({"type": "section", "fields": fields})

    # Per-invariant, always. The dashboard's rule applies here too.
    if invariants:
        lines = []
        for inv in invariants:
            tick = {"held": "✓", "falsified": "✗", "invalid": "?"}.get(inv["outcome"], "·")
            worst = "—" if inv.get("worstValue") is None else f"{inv['worstValue']:.4g}"
            lines.append(f"{tick} `{inv['name']}` worst {worst} vs {inv.get('threshold')} "
                         f"· breached {inv.get('breachedForS', 0):.0f}s")
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "\n".join(lines[:10])}})

    if grafana_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Investigate in Grafana"},
                "url": grafana_url,
            }],
        })

    return blocks


# --------------------------------------------------------------------------- #
# Score regression, and the button that costs ninety minutes (§20.3).
# --------------------------------------------------------------------------- #

def bisect_workflow_url(repo: str | None = None, *, experiment: str,
                        good: str, bad: str) -> str | None:
    """Deep link to the bisect workflow's dispatch form, pre-filled.

    A URL BUTTON, deliberately, not an interactive one. A Block Kit button with
    an `action_id` posts to a request URL, which would make ChaosProof an
    inbound HTTP endpoint needing Slack signature verification, replay-window
    checks and a secret to rotate — a whole security surface, added so that a
    button could skip one page load. ChaosProof is otherwise a net CONSUMER of
    Prometheus and Alertmanager and receives no inbound webhooks at all, and
    that property is worth more than the click.

    The link lands on GitHub's own dispatch form, which already authenticates
    the human, records who pressed it, and shows the inputs before they run.
    The authorisation §20.3 asks for is a person deciding — not a particular
    widget.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    query = urllib.parse.urlencode({
        "experiment": experiment, "good": good, "bad": bad})
    return (f"https://github.com/{repo}/actions/workflows/bisect.yml"
            f"?{query}")


def regression_blocks(*, experiment: str, score_from: float, score_to: float,
                      epoch: str | None, window_days: int,
                      sigma: float | None, cost_label: str,
                      separable: bool, separability_note: str,
                      workflow_url: str | None = None) -> list[dict]:
    """The message that reports a score regression and offers to diagnose it.

    Three things are on it before the button, because the button costs ninety
    minutes of exclusive cluster time:

      1. The drop, WITH its epoch. A drop across an epoch boundary is not a
         regression, it is a change to the scorer, and the trend chart already
         says so with a vertical bar.
      2. Sigma, so a reader can see for themselves whether the drop is larger
         than the experiment's ordinary noise.
      3. Whether the arithmetic can separate the two ends AT ALL. When it
         cannot, the button is not rendered — offering a search that is
         guaranteed to abandon is worse than offering nothing, because someone
         will press it, wait, and conclude the tool is broken.
    """
    delta = abs(score_from - score_to)
    direction = "fell" if score_to < score_from else "rose"

    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f":chart_with_downwards_trend: {experiment} — score "
                          f"{direction} {delta:.3f}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn",
             "text": f"*Score* `{score_from:.4f}` → `{score_to:.4f}`\n"
                     f"*Epoch* `{(epoch or 'unknown')[:12]}`"},
            {"type": "mrkdwn",
             "text": f"*Window* {window_days} day(s)\n"
                     f"*Sigma* " + (f"`{sigma:.4f}` on unchanged code"
                                    if sigma is not None
                                    else "_uncharacterised_")},
        ]},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": separability_note}]},
    ]

    if not separable:
        # No button. Naming the reason is the actionable part: the fix is to
        # reduce the experiment's variance, not to look harder at the commits.
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
            ":no_bell: *Not bisectable.* This drop is inside the experiment's "
            "own noise, so a binary search over it would abandon or, worse, "
            "converge on a commit chosen by variance. Reduce sigma first."}})
        return blocks

    if workflow_url:
        blocks.append({"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": cost_label},
            "url": workflow_url,
            # Ninety minutes of the one cluster is not an undo-able click.
            "style": "primary",
            "confirm": {
                "title": {"type": "plain_text", "text": "Start a bisection?"},
                "text": {"type": "mrkdwn", "text":
                         f"*{cost_label}* of exclusive cluster time, in the "
                         f"01:00–05:00 window. The daily chaos schedule does not "
                         f"run while this does."},
                "confirm": {"type": "plain_text", "text": "Open the form"},
                "deny": {"type": "plain_text", "text": "Cancel"}},
        }]})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"_{cost_label} — run it with_ "
            f"`chaosctl bisect {experiment} --good <sha> --bad <sha> --apply`"}]})

    return blocks


def post(blocks: list[dict], webhook: str | None = None) -> bool:
    """Returns True when actually sent. No webhook is not an error: the run
    already succeeded, and a missing integration must not fail an experiment."""
    url = webhook or os.environ.get(WEBHOOK_ENV)
    if not url:
        return False
    body = json.dumps({"blocks": blocks}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # Slack being down must never fail a run or lose a verdict — the
        # evidence store already has it.
        print(f"slack post failed (verdict already persisted): {e}")
        return False
