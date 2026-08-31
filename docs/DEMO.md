# The five-minute demo

**Rehearse this out loud.** The order is deliberate and it is the most important
thing on this page: **it opens with a bug in this project's own tooling.**

An engineer who begins by showing how their instruments used to lie to them —
and the mechanism that now stops it — has established something no green
dashboard can buy. Leading with a passing experiment invites the question "how
do you know that number is real?" as a challenge. Leading with the `INVALID`
verdict answers it before it is asked, and every number afterwards inherits the
credit.

---

## Before you start

```bash
make bootstrap        # cluster + platform + evidence store + app + load
```
```bash
cd dashboard && READ_ONLY=false npm run build && npm start
```

Have open: the dashboard, a terminal in `chaos-framework/`, and a second
terminal for the load plane. Have the Grafana experiment dashboard on a third
tab but do not lead with it.

---

## 1 · (0:00–0:40) The validity moment — lead with your own bug

Dashboard showing a healthy score.

> "Before I show you a pass, here's a failure of my own tooling."

```bash
make load-stop
```
```bash
make experiment NAME=pod_kill_payment_svc
```

**Verdict: `INVALID` — achieved 0 rps, floor 90.**

> "My first version scored this as a perfect recovery. No traffic, no errors, no
> evidence — and a green badge. The system under test was never asked a
> question, and the framework reported that it answered correctly.
>
> `INVALID` is not a zero. It doesn't contribute to the score at all, because a
> zero would drag the average down as though the system had failed, when in fact
> nothing was measured."

*This is GATE 2, recorded 23 Aug 2026, in both directions.*

**If it goes wrong:** the verdict is the point, so there is nothing to recover
from — an `INVALID` is a successful demo of this beat.

---

## 2 · (0:40–1:40) A real experiment

```bash
make load
```
```bash
make experiment NAME=pod_kill_payment_svc
```

Talk over the live timeline:

- fault applied
- **client errors at ~1.2s**
- **server-side metrics react at ~6s** — *point at the gap*
- replicas restored
- SLI held throughout

> "That gap is the whole reason client-side SLIs are primary. Server-side
> metrics cannot see a request that never reached a server — during endpoint
> convergence the surviving replicas look *healthier*, because they're handling
> a smaller, cleaner load. The gap is roughly five seconds and it is
> irreducible: scrape interval plus rate window plus evaluation. I subtract it
> before I report alert latency, so the number is honest rather than
> flattering."

**Verdict: `HYPOTHESIS_HELD`, per invariant** — four rows, each with its worst
value, its threshold, and how long it was breached.

---

## 3 · (1:40–2:30) The abort — the mechanism that makes this safe

Trigger the partition experiment with the fallback disabled, in
`chaos-staging`.

Client availability crosses 80% → **`promProbe` trips → fault halted at ~14s →
cleanup saga runs → steady state re-established.**

> "It stopped itself. The probe runs in Continuous mode with `stopOnFailure`,
> and there's an independent Python watchdog beside it that doesn't share a
> failure domain — if the probe path is what broke, the watchdog still fires.
>
> Cleanup runs on every exit path, including a crash mid-fault, with a CronJob
> as the out-of-band sweeper. That's the part that isn't optional. A cleanup
> that only runs on the happy path leaves a `tc netem` rule on a node the next
> time the process is killed.
>
> **This is the mechanism that would let me run this beyond CI.**"

---

## 4 · (2:30–3:15) The counterfactual — and the one that says nothing

The ROI panel: circuit breaker on vs off, n=5 per arm, interleaved, box plots.

> "~5,000 failed requests prevented per incident. The IQRs don't overlap, so
> I'll report the delta.
>
> Next to it, the retry pattern: the IQRs *do* overlap, so the panel says
> **inconclusive** and shows no number. Five runs per arm is not enough to
> resolve that difference, and reporting a median delta anyway would be the most
> professional-looking lie in the project."

*GATE 10 produced a real `inconclusive`, and it stayed in.*

---

## 5 · (3:15–4:15) The CI gate

Open the PR that removes `@CircuitBreaker`.

**Gate fails:** `HYPOTHESIS_FALSIFIED` — `circuit_breaker_opens` never
activated.

Then the second PR that weakens a contract → **contract-diff comment tagging the
owners.**

> "And the honest question about chaos in CI: why would my team still have this
> enabled in six months? A flaky gate gets disabled — `--no-verify`, an admin
> merge, or someone deletes the workflow. Every abandoned CI gate was abandoned
> for that reason.
>
> So ChaosProof measures its own variance. It runs each experiment against
> unchanged code, computes σ of the score and the verdict flip rate, and any
> experiment that can't hold σ under 0.05 is **automatically demoted to advisory
> and files an issue against itself**. New experiments start advisory and earn
> the right to block a merge after twenty clean runs.
>
> Right now every experiment in the repo is advisory, because I haven't run
> twenty of anything for real. That's the rule working, not the rule being
> skipped."

---

## 6 · (4:15–5:00) The receipt

Turn wifi off. Genuinely off.

```bash
python -m src.chaosctl replay 21a5bf73
```

Byte-identical output.

> "Every number on that dashboard has a content-addressed evidence bundle behind
> it — the sha256 of the canonical JSON *is* the run's identity, and
> `sha256sum bundles/<sha>.json` matches the filename. That claim is checkable
> from outside the project, which is why building it caught a defect: the first
> version stored the *sealed* bundle, including its own digest field, so the
> file hash couldn't match by construction. The test passed only because it
> stripped the field back out first. I changed the storage to make the claim
> true rather than softening the claim.
>
> And the trend line has a vertical bar in week two where my scoring changed. I
> retro-scored everything before it under the new epoch, so the comparison is
> real — and the line **stops** at the boundary rather than crossing it. Without
> that, some of my improvement would have been me editing the scorer, and I'd
> rather show you the seam than have you find it."

*GATE 11, 31 Aug 2026 — verified with sockets, connections and DNS disabled at
the process level rather than mocked in a test, so a locally-running Prometheus
could not mask a hidden dependency.*

---

## If you have another sixty seconds

```bash
python -m src.chaosctl bisect pod_kill_payment_svc --good <sha> --bad <sha> --estimate
```

> "The score dropped 0.16 over a week — which commit? The arithmetic first: with
> σ = 0.06 on unchanged code, one run per candidate needs a separation of 0.24
> to be trustworthy and I only have 0.16, so a single run would misclassify —
> and **one misclassification sends a binary search down the wrong half
> permanently**, because the correct half is never revisited. Three reps put σ of
> the mean at 0.035, the required separation at 0.14, and 0.16 clears it.
>
> That costs six candidates at eighteen minutes each. So it doesn't run
> automatically — the Slack regression message carries a button that says
> *'Bisect (est. 6 candidates, ~90 min)'* and a human decides. Automatic
> bisection on every score dip is a denial of service against the daily
> schedule.
>
> And if a candidate lands inside the flakiness band, it returns **abandoned**.
> It does not guess."

---

## Recovery notes

| If | Do |
|---|---|
| The cluster is slow to come up | Start `make bootstrap` before the call, not during it |
| An experiment returns `SKIPPED` | That is pre-flight refusing to inject — narrate it as beat 3 arriving early |
| Slack is unconfigured | It is optional by design; the reporter renders the blocks and returns |
| The framework API is down | The dashboard renders an error panel rather than blanking — say so and move on |
| Anything at all returns `INVALID` | Read it out. It is the thesis, not an interruption |

**The demo has no recovery step for a failing experiment, because a failing
experiment is not a failure of the demo.** Six of the seven possible verdicts
are legitimate outcomes to show. The only one worth avoiding is a framework
`ERROR`, and that is the single red marker in the whole system.
