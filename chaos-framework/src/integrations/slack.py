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
