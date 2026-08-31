# FW-003 — Game-day scoring

**Status:** deferred · **Date:** 2026-08-31

## What it would be

Scoring a human incident response the way ChaosProof scores a system: inject a
fault during a scheduled game day, then measure time-to-detect, time-to-first-
correct-hypothesis, time-to-mitigate, and how much of the runbook was actually
followed.

## Why it is not built

**It scores humans, and that is a different instrument with a different failure
mode.**

Every design decision in this project assumes the subject under test is
deterministic enough to be replayed. The scoring epoch exists so two numbers
computed by the same scorer are comparable. The flakiness window exists so an
experiment's variance can be measured *on unchanged code*. The evidence bundle
exists so a verdict can be re-derived offline.

None of that transfers:

- **There is no "unchanged code" for a person.** The second time someone
  responds to the same injected fault, they have seen it before. σ cannot be
  measured on a subject that learns, so there is no way to know whether a
  response-time difference is a real improvement or noise — which is the exact
  question this project refuses to guess at elsewhere.
- **The measurement changes the subject.** A responder who knows they are being
  timed responds differently. The availability SLI has no such problem.
- **A low score has a different meaning.** A falsified hypothesis is a finding
  about a system. A low game-day score is a finding about a colleague, and it
  will be read as a performance review whatever the documentation says. That
  changes what people do during the game day, which corrupts the measurement,
  which is a failure mode the system-facing scorer simply does not have.

The useful part is separable and cheaper: **the fault injection and the evidence
bundle are already there.** A game day can use `chaosctl` today, and the bundle
gives an accurate, timestamped record of what the system did — which is the
input a human retrospective needs. What is deferred is *scoring the humans*, not
running the exercise.

## What would change it

An organisation running game days regularly enough that a trend means something,
that wants them measured, and that has decided explicitly how the numbers relate
to performance management. That last part is not a technical prerequisite, and
it is the one that actually gates this.
