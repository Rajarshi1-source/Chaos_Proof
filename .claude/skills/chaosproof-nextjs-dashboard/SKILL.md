---
name: chaosproof-nextjs-dashboard
description: "Build ChaosProof's resilience dashboard on Next.js 16.3 App Router with Node 24 LTS, Tailwind, and shadcn/ui in a dark-first operations theme. Use for ANY dashboard work: the verdict discriminated union and badge treatment, the epoch-aware trend chart with labelled boundaries, the SLI validity strip, the client-versus-server availability overlay, the recovery timeline with its irreducible-latency marker, counterfactual box plots, the flakiness table, contract coverage, and loading, empty, error, and stale states. Trigger on App Router, server component, route handler, VerdictBadge, trend chart, epoch boundary, validity strip, recovery timeline, counterfactual plot, flakiness table, Tailwind, shadcn, or dark mode. MANDATE: never style INVALID as pass or fail, never connect a trend line across an epoch boundary, never show a counterfactual delta when the interquartile ranges overlap, and never ship trigger controls in the public read-only build."
---

# ChaosProof Resilience Dashboard

The dashboard's job is to make a claim checkable: *the system survived these faults, here is the evidence for each one, and here is where I couldn't measure.* Every design decision serves that — including the ones that make the project look worse.

## Version floor

| Component | Pin | Why |
|---|---|---|
| Node.js | **24 LTS** | Node 20 reached end of life April 2026 |
| Next.js | **16.3.x**, App Router | Next 14 is EOL; Next 15 hits EOL Oct 2026. Turbopack is the default bundler in 16 |
| TypeScript | strict | Discriminated unions carry the verdict vocabulary |
| Tailwind + shadcn/ui | current, **copied into `components/ui`** | Dark-first; SRE dashboards are always dark |

## Layout

```
dashboard/src/
├── app/
│   ├── layout.tsx                     # dark theme root, nav
│   ├── page.tsx                       # gauge + epoch-aware trend + latest per experiment
│   ├── executions/[id]/page.tsx        # per-invariant detail, dual-source overlay
│   ├── experiments/page.tsx            # registry: hypothesis version, sigma, gating status
│   ├── contracts/page.tsx              # clause outcomes + coverage ratio
│   ├── counterfactuals/page.tsx        # box plots with IQRs
│   ├── slos/page.tsx                   # error budget + gate state
│   └── api/                            # score  trends  executions  contracts  health
├── components/
│   ├── dashboard/                      # ResilienceGauge  ScoreTrendChart  ExperimentCards
│   │                                   # ValidityStrip  RecoveryTimeline  ErrorBudgetBurn
│   │                                   # FlakinessTable  CounterfactualPlot
│   ├── common/                         # VerdictBadge  InvariantRow  LoadingSkeleton
│   └── ui/                             # shadcn/ui, COPIED IN and edited here
└── types/index.ts
```

Route Handlers under `app/api/` proxy the framework API rather than reading PostgreSQL directly. One query layer, one set of types — and the read-only public build can point at a seeded API with no cluster credentials anywhere in the front end.

Default to Server Components. Reach for `'use client'` only for real interactivity: the live execution feed, timeline filters, `JsonDiff` expand/collapse, and the counterfactual plot's assumption inputs. Execution detail pages are fully static once an execution closes — cache accordingly rather than re-fetching a settled record.

## The verdict vocabulary drives the type system

```ts
type Verdict =
  | { kind: 'held' }
  | { kind: 'falsified'; falsifiedInvariants: InvariantOutcome[] }
  | { kind: 'invalid'; reason: string }        // could not measure
  | { kind: 'skipped'; reason: string }        // pre-flight refused
  | { kind: 'denied'; rule: string }           // policy refused
  | { kind: 'aborted'; reason: string }        // halted mid-fault
  | { kind: 'error'; message: string };
```

Drive `VerdictBadge` from this discriminated union so **a new verdict is a compile error, not a silently grey badge.**

### How each verdict must read

| Verdict | Visual treatment | Why |
|---|---|---|
| `held` | Green, unremarkable | The baseline |
| `falsified` | Amber, **naming the specific invariant** | "The experiment failed" is not actionable; "client_p99_holds breached 800ms for 34s" is |
| **`invalid`** | **Distinct colour — not red, not green.** Slate or hatched | It is neither pass nor fail. Styling it red implies the *system* failed when in fact **nothing was measured** |
| `skipped` / `denied` | Neutral with the reason inline | **First-class outcomes, not errors.** "The framework correctly refused to run" proves the guardrails are load-bearing |
| `aborted` | Amber with a link to the tripped abort condition | The safety plane working as designed — arguably the best thing on the dashboard |

**Never style `skipped`, `denied`, or `aborted` as failures.** A red "denied" badge would contradict the project's entire thesis, which is that refusing to act and refusing to answer are correct behaviours.

## The trend chart — epoch boundaries are mandatory

A resilience score is only comparable to another computed the same way. The chart must therefore:

1. **Connect points only within one scoring epoch.** Segments break at boundaries; never interpolate across one.
2. **Draw each boundary as a labelled vertical line** with the change reason from `scoring_epochs.change_reason` ("added alert validation to the weighted checks").
3. **Show retro-scored points distinctly** (e.g. hollow markers) so a viewer can see which history was re-derived under the current epoch rather than measured under it.
4. **Never cache trend data across epochs** — key the cache by epoch, or the comparability bug reappears at the presentation layer.
5. **Exclude `invalid`, `skipped`, `denied`, `aborted`, and counterfactual executions** from the trend. Counterfactual runs are deliberately-degraded configurations and would poison it.

Two honestly separated segments are far more persuasive than one smooth line a reviewer can dismantle. Build the seam in deliberately.

## The validity strip

A horizontal band under the trend showing, per run, achieved rps against the declared floor — red where a run was `INVALID`. This is the panel that makes the project's core discipline visible at a glance, and it is the first thing to show in a demo: *"before I show you a pass, here's my framework refusing to score a run it couldn't measure."*

## Client-vs-server availability overlay

Plot both series on the execution detail page and **shade the gap**. The gap is an estimate of requests that died before reaching any server — during a pod kill, server-side availability looks *better* than reality because a request that fails at connection establishment never increments a server-side counter. Label the shaded region explicitly; it is a genuinely interesting signal, not a rendering artefact.

## The recovery timeline

Distinct markers, in this order: fault injected · first client failure · first server-visible failure · alert fired · **the irreducible-latency marker** · SLI restored · recovery hold satisfied · cleanup complete.

The irreducible-latency marker matters: it shows the floor below which no alert could possibly fire (scrape + evaluation + `for:`), so a viewer can see that a 71-second alert on a 95-second floor fired as fast as physics allowed. Without it, honest alerting looks slow.

## Counterfactual panel

Box plots per arm, **not two bars**. Requirements:

- Show **n**, medians, and interquartile ranges.
- When IQRs overlap, render the verdict as **`inconclusive`** and suppress the delta figure. Do not show a median difference from overlapping distributions — that is how dashboards become fiction.
- Show cost-translation **assumptions as editable inputs on the panel** (revenue per checkout, conversion loss, incidents per year), each labelled as an assumption. An unqualified rupee figure invites dismantling.

## Flakiness table

Per experiment: σ, verdict-flip rate, gating status, and quarantine reason where set. Quarantined experiments stay visible — quarantine is not deletion. This panel is the answer to "why would your team still have this gate enabled in six months," so make it legible rather than buried.

## States that actually happen

Build all four for every data view:

- **Loading** — skeletons matching the final layout so nothing jumps.
- **Empty** — "no executions yet" is the *normal* state of a fresh install and the first thing a visitor sees. Make it say how to trigger the demo.
- **Error** — framework or database unreachable. Degrade to cached data with a staleness note rather than a blank page: *a dashboard that errors during a live demo is worse than one showing 30-second-old data.*
- **Stale** — cache-aside with stale-while-revalidate; show the data's age when served stale.

## Styling

Dark-first. Theme through semantic tokens in CSS variables, never hardcoded colours — the verdict vocabulary needs consistent semantics across badges, timeline markers, and charts, and that only holds with one source of colour truth. In particular, `invalid` needs its own token distinct from both success and failure.

shadcn/ui components are **copied into `components/ui` and edited there**, never installed as an npm dependency. Preserve Radix accessibility: keyboard navigation on the timeline, focus management in dialogs, `aria-live` on the live execution feed.

## Read-only in public

The public build is seeded with real recorded executions plus rendered `chaosctl replay` output. **Manual-trigger controls must be absent from the read-only build via a build-time flag**, not merely disabled — a control that 404s invites someone to find out why. No endpoint that can inject a fault is reachable from the internet.

Keep the replay page's rendering aligned with the CLI's output format; that format doubles as a golden-file test.

## Non-negotiables

1. **Never style `invalid` as a failure or a pass.** It is a third thing.
2. **Never connect a trend line across an epoch boundary.**
3. **Never show a counterfactual delta when the IQRs overlap.**
4. **Never render a bare score without its epoch.**
5. **Never include `invalid`/`skipped`/`denied`/`aborted` or counterfactual runs in an aggregate.**
6. **Never ship trigger controls in the public build.**
7. **Never show a verdict without naming the invariant** that produced it.
