# ChaosProof — Chaos Engineering Lab with Automated Recovery Testing

## Complete Implementation Plan for Junior SRE Interview (2026)

**Revision 2 — August 2026.** Supersedes Rev 1 and folds in the separate innovative-features document.

---

> ### Revision 2 — What Changed and Why
>
> Rev 1 was audited line-by-line against the brief, and the separate features document was merged in rather than left as a parallel wishlist. **The architecture is sound** — LitmusChaos as the fault injector, the four-layer validation framework, plain PostgreSQL over TimescaleDB, Redis Streams with a single consumer for serial execution, chaos in CI as the headline, the composite resilience score. Rev 2 keeps every one of those calls. What it adds is the difference between *a chaos lab that produces numbers* and *a chaos lab whose numbers are trustworthy*:
>
> **1. Six real defects fixed (§C).** The most serious: **Rev 1 has no load generator.** Every SLI in the plan is a ratio over `http_server_requests_seconds_count` — and with no traffic during an experiment that ratio is 0/0. A pod-kill experiment against an idle cluster always "passes," because nothing was ever failing. Also fixed: recovery time measured against a 30-second SLO using 5-minute rate windows (physically impossible — the metric lags the SLO by an order of magnitude); non-applicable validation checks silently scored as 0.5, which inflates every score containing one; the 45%→92% trend comparing scores computed under different weights and experiment sets; a "circuit breaker on chaos itself" claimed in the interview answers but implemented nowhere; and error budget tracked but never enforced.
>
> **2. Stack re-pinned against reality as of 19 August 2026 (§B).** LitmusChaos **3.30+** (monthly cadence — 3.30.0 shipped June 2026), Spring Boot **4.0.x** with **Resilience4j 3.x** on **Java 21**, **k6 2.2.x** plus **k6 Operator v1.0** as the newly-added load plane, Python **3.14**, PostgreSQL **18.6**, Grafana **13.0.x**, Prometheus **3.x** chart-pinned, Kubernetes **1.36** demo / **1.35** floor, Helm **4.2.x**, Next.js **16.3**, Valkey **9.1**.
>
> **3. A measurement-integrity plane (§16) — the section that makes the rest defensible.** Load is generated in-cluster by k6 under an **open workload model** (constant arrival rate, not constant VUs — a closed model silently reduces offered load when the system slows, hiding the very impact you're measuring). Client-side k6 metrics become a second SLI source alongside server-side Prometheus, because **server-side metrics structurally under-report pod kills**: a request that fails at connection establishment never reaches a server and never increments a server-side counter. Every verdict now has a third possible outcome — `INVALID` — when traffic was below the floor needed to measure anything.
>
> **4. Ten differentiators, promoted from wishlist into the plan (§14–§22).** The features document ranked eleven ideas across four tiers and left them outside the build. Rev 2 moves the load-bearing ones into the core and specifies them:
> - **§14 Steady-state hypothesis engine.** Every experiment declares a falsifiable hypothesis with quantified invariants, and the framework reports `HYPOTHESIS_HELD` / `HYPOTHESIS_FALSIFIED` / `INVALID` rather than a bare pass/fail. Straight from *Principles of Chaos Engineering*.
> - **§15 Safety plane.** Blast radius computed *before* injection, abort-on-harm during the run via Litmus probes plus an independent watchdog, and an **error-budget-aware gate** — chaos does not run when the budget is already spent, which is the actual SRE position.
> - **§16 Measurement integrity.** The load plane, SLI validity gating, high-resolution recovery measurement, and honest alert-latency accounting that subtracts irreducible detection lag instead of grading your own Prometheus config.
> - **§17 Counterfactual resilience analysis.** The same fault run with a pattern enabled and disabled, repeated *n* times, reported as median with interquartile range rather than a single seductive number.
> - **§18 Cascading failure scenarios.** A fault DAG with temporal triggers, because real outages are cascades and Chaos Monkey cannot express one.
> - **§19 Service resilience contracts.** Each service declares what it tolerates from dependencies; ChaosProof generates the experiments that validate the claim. Consumer-driven contract testing, applied to failure.
> - **§20 Resilience regression bisection** with the flakiness arithmetic that makes it actually converge.
> - **§21 The DevOps/MLOps wrapper** the brief asked for and Rev 1 omitted entirely: scoring epochs, a hermetic replay eval that gates CI, **flakiness quarantine** (a flaky chaos gate gets disabled by developers within a week — so ChaosProof measures its own variance and demotes unstable experiments to advisory automatically), and determinism digests.
> - **§22 Postmortem-as-code** generated from a content-addressed evidence bundle, with the LLM confined to prose it cannot invent.
>
> **5. The brief's missing sections written properly (§23–§29):** resilience patterns of ChaosProof itself, availability and consistency, a 22-row mitigation table, deployment strategies, README blueprint, expanded interview prep, and a build order with a competitive grid and a fact-check appendix.
>
> **On "beat LitmusChaos and Chaos Monkey" — a framing correction worth making out loud.** ChaosProof *runs on* LitmusChaos, so competing with it is a category error: Litmus is the fault injector, and rebuilding fault injection would be strictly worse than using the CNCF-backed one. Chaos Monkey is a random instance terminator from 2011 — a much lower bar than it sounds. The honest competitive claim is narrower and stronger: **Litmus injects faults and tells you the experiment ran; ChaosProof decides whether the system's behaviour was acceptable, and refuses to answer when it cannot measure.** That verification-and-safety layer is what §14–§22 build, and it is genuinely thin in every tool surveyed in §29.2.

---

## §A. Completeness Audit — Rev 1 vs the Brief

| # | Brief asked for | In Rev 1? | Where | Rev 2 action |
|---|---|---|---|---|
| 1 | Tech stack, justified | ✅ | §2 | Re-pinned (§B); **load plane added** (k6 + k6 Operator), feature-flag plane, contract registry |
| 2 | Exact versions | ⚠️ loose (`3.x`, `Redis 7`) | §2 | **Rewritten** (§B) + fact-check appendix (§29.6) |
| 3 | MVP blueprint | ✅ | §3 | Every experiment gains a hypothesis, abort conditions, and an SLI-validity precondition (§14–§16) |
| 4 | Detailed system design | ✅ | §4 | Load → hypothesis → safety gate → inject → measure → verdict stages added |
| 5 | Detailed system architecture | ✅ | §4 | Load plane, safety plane, contract registry, evidence store added |
| 6 | High-level design | ✅ | §4 | Reshaped around the six-stage pipeline |
| 7 | Low-level design | ✅ | §5 | `ExperimentRunner` rewritten (§14.4); baseline/observe queries corrected (§16.3) |
| 8 | Detailed database design | ✅ | §6.2 | New tables: hypotheses, hypothesis_results, load_runs, sli_samples, scoring_epochs, contracts, counterfactuals, flakiness (§C.7) |
| 9 | Database choice + matrix | ✅ | §6.1 | Verdict unchanged (plain PostgreSQL) — upgraded to **18.6**; CosmosDB and generic NoSQL rows added, which the brief asked for and Rev 1 skipped |
| 10 | Cache & messaging choice | ⚠️ Zookeeper and RabbitMQ not addressed | §7 | Verdict unchanged (Redis Streams) — **Zookeeper and RabbitMQ answered** (§C.8); lock now fenced |
| 11 | Design patterns | ✅ 6 | §8 | +5 (hypothesis/specification, saga for cleanup, token bucket, circuit breaker as policy, content-addressed evidence) (§C.9) |
| 12 | Docker & K8s | ✅ | §9 | Pinned compose, Helm 4 notes, RBAC tightened and justified verb-by-verb (§26.2) |
| 13 | **DevOps/MLOps wrapper** | ❌ **absent** | — | **§21** — scoring epochs, hermetic replay eval gating CI, flakiness quarantine, determinism digest |
| 14 | Resilience patterns | ⚠️ patterns of the *target app* only | §3, §8.5 | **§23** — ChaosProof's own resilience, full table |
| 15 | Mitigation strategies | ❌ | — | **§25** — 22-row failure-mode table |
| 16 | Availability & consistency | ❌ | — | **§24** |
| 17 | Deployment strategies | ⚠️ partial | §9, §13 | **§26** — environments, canary, migrations, rollback, read-only public demo |
| 18 | Interview prep | ✅ 9 Q&A | §12 | **+15 Q&A** (§28), including the four questions Rev 1 would have failed |
| 19 | README + architecture diagram + screenshots | ⚠️ checklist line only | §13 | **§27** — full blueprint |
| 20 | CI/CD (Actions → registry → deploy) | ✅ | §10 | + flakiness gate, replay eval, contract gate, scoring-epoch check (§21.5) |
| 21 | docker-compose.yml | ✅ | §9.1 | Tags pinned, obsolete `version:` key removed, load generator added |
| 22 | Monitoring dashboard | ✅ | §11 | + hypothesis panel, SLI-validity panel, flakiness panel, error-budget gate state (§16.6) |
| 23 | Documented "scaling decision" | ✅ 1 | §11 | + a second (open vs closed workload model) (§16.2) and a third (bisection cost) (§20.3) |
| 24 | Public deployment + live link | ✅ | §13 | Two-tier read-only design (§26.4) |
| 25 | **Differentiators vs existing tools** | ⚠️ in a separate doc, unbuilt | — | **§14–§22 + the grid in §29.2** |
| 26 | Best DB among PG/NoSQL/Timescale/Cosmos/Cassandra/Mongo | ⚠️ Cosmos + generic NoSQL missing | §6.1 | Completed (§C.7) |
| 27 | Cache among Redis/Kafka/Zookeeper/RabbitMQ | ⚠️ only Kafka rejected | §7 | Completed (§C.8) |
| 28 | Scalability decisions | ✅ 1 | §11 | Three now, each with the rejected option and the accepted trade-off |

**Verdict: Rev 1 was structurally complete and measurably unsound.** The scaffolding was right — experiments, validators, scoring, CI gate, dashboard. What it could not do is produce a number anyone should believe, because nothing generated traffic and the recovery window was shorter than the metric's own lag. §C fixes the measurement; §14–§22 make the result defensible; §21 adds the wrapper the brief asked for.

---

## §B. Exact Version Matrix — verified 19 August 2026

Pin everything. Sources and dates in **§29.6**.

| Component | Rev 1 | **Rev 2 — pin this** | Why |
|---|---|---|---|
| **LitmusChaos** | `3.x` | **3.30.0 or later — pin the exact monthly tag** | Litmus releases on a monthly cadence (3.25.0 Jan → 3.30.0 Jun 2026), so `3.x` is not a pin. Three recent fixes matter directly: **3.29.0** fixed *duplicate chaos experiment triggers under concurrent reconciles* (your serial-execution guarantee depended on luck before this) and added *stopping experiments when infra is disconnected*; **3.28.0** fixed a *stale config leak across multiple probes of the same type* — Rev 2 uses several probes per experiment, so this is load-bearing; **3.27.0** returns 503 when the DB is down *so probe detection stays accurate*, and removed the 1024-char CMD-probe limit |
| **Kubernetes** | not pinned | **1.36** demo (`kindest/node:v1.36.x`); **1.35 floor** | 1.34 entered maintenance Aug 2026. 1.35+ requires **cgroup v2**, which is what makes CPU-throttle and memory-pressure signals trustworthy — and this project lives on those signals |
| **Target app** | Spring Boot 3.x, Java 21 | **Spring Boot 4.0.x + Java 21** | Boot 3.x is at/after OSS end-of-life. Java 21 stays: **Resilience4j 3 requires Java 21** |
| **Resilience4j** | unpinned | **3.x** with `resilience4j-spring-boot4` + **`resilience4j-micrometer`** | Two traps. (1) Spring Boot 4 support arrived in the **2.4.0** line via a *new* artifact `resilience4j-spring-boot4` — not `-spring-boot3`. (2) That artifact was **omitted from the BOM** (issue #2427, Mar 2026), so BOM-managed builds fail to resolve it; verify the BOM before trusting it, otherwise pin the module version explicitly. And `resilience4j-micrometer` is **not transitive** — without it there are no `resilience4j_*` Prometheus series and every pattern check in this project silently has nothing to read |
| **Load generator** | ❌ **absent** | **k6 2.2.x** + **k6 Operator v1.0** | The defect in §C.1. k6 2.0 (May 2026) added OpenTelemetry output, structured JSON output, and a `run-k6-action` GitHub Action; the Operator v1.0 runs distributed load in-cluster as a CR, which is what an experiment needs |
| **Python** | 3.12 | **3.14** (`python:3.14-slim`, non-root) | Current line; pin `requires-python = ">=3.14"` |
| **Prometheus** | unpinned | **3.x, chart-pinned** via `kube-prometheus-stack` (explicit chart version) | Never `latest`. **And override `scrapeInterval` to 5s for the target app's ServiceMonitor** — the 30s default cannot resolve a 30s recovery SLO (§16.3) |
| **Alertmanager** | unpinned | **0.33.1** | **API v1 was removed in 0.27** — the alert validator must query `/api/v2/alerts`. Rev 1's flow diagram already used v2; keep it |
| **Grafana** | unpinned (`:latest` in compose) | **13.0.x** (13.0.0 released 14 Apr 2026), dashboards provisioned as JSON in-repo | Reproducibility; `:latest` in a resilience project is self-refuting |
| **PostgreSQL** | 16 | **18.6** (`postgres:18.6-alpine`) | 18.6 released 11 Aug 2026. `uuidv7()` gives timestamp-ordered keys for the evidence and sample tables; PG 19 is beta — do not ship a beta database |
| **Cache / queue** | Redis 7 | **Valkey 9.1** (default) or **Redis 8.x** | Valkey 9.1 is BSD-3 and drop-in; be ready to explain the Redis licence change. **Streams** remain the queue |
| **Node.js / Next.js** | Next 14 | **Node 24 LTS**, **Next.js 16.3.x** | Next 14 is EOL; Next 15 hits EOL Oct 2026. Turbopack is the default bundler in 16 |
| **Helm** | 3 | **4.2.x** | Helm 3 bug fixes ended 8 July 2026. Three gotchas: server-side apply is default for new installs, `--wait` needs the **`watch`** RBAC verb, `--post-renderer` must now be a plugin |
| **Slack SDK** | unpinned | **slack-bolt 1.28.x** (+ `slack_sdk`) | 1.28 added Python 3.14 support; use `AsyncApp` with the FastAPI adapter, not a second web server |
| **Framework API** | implicit | **FastAPI 0.128.x** + `uvicorn[standard]` | The framework needs an HTTP surface for the dashboard and manual triggers |
| **Contract/policy eval** | absent | **CEL via `cel-python`** | §15.4 safety rules and §19 contract assertions are declarative and reviewed in git |
| **Signing** | absent | **cosign (keyless OIDC)** in CI | Signs images *and* evidence bundles (§22.3) |

### API and feature status this plan depends on

| Capability | Status (Aug 2026) | Why ChaosProof cares |
|---|---|---|
| **Litmus probes** (httpProbe, cmdProbe, promProbe, k8sProbe) | Mature; **stale-config-across-same-type-probes fixed in 3.28.0**; CMD length limit removed in 3.27.0 | §15.3 — probes are the *in-experiment* abort mechanism. Multiple probes of the same type per experiment is exactly the pattern that was broken before 3.28.0 |
| **Litmus Prometheus metrics** | Shipped in **3.29.0** | ChaosProof can now monitor the chaos platform itself, not just the target (§23) |
| **cgroup v2** | Required by K8s 1.35+ | Reliable CPU-throttle ratio and memory-pressure signals |
| **PSI metrics** (`KubeletPSI`) | beta 1.34 → **GA 1.36** | §16.4 — "did it actually recover?" answered with pressure, not just with "the alert stopped firing" |
| **Alertmanager API v2 only** | v1 removed in 0.27 | The alert validator and any silence automation |
| **Spring Boot 4 native resilience** (`@Retryable`, `@ConcurrencyLimit`, `@EnableResilientMethods`) | Shipped in Boot 4 | **A decision, not a default — see §C.6.** Native Spring gives retry and concurrency limiting but **no circuit breaker**, and does not emit the `resilience4j_*` series this project validates. ChaosProof keeps Resilience4j *because the patterns must be observable to be verifiable* |
| **k6 Operator v1.0** | GA with k6 2.0 (May 2026) | Load runs in-cluster as a `TestRun` CR — no laptop in the measurement path |
| **xk6-disruptor** | k6 extension, injects K8s faults | Considered and rejected as the injector (§29.2), but worth knowing: it means k6 alone can do a thin version of this project |

> Interview soundbite: *"Two pins in this project aren't arbitrary. Kubernetes 1.35 is my floor because cgroup v2 is mandatory there, and my CPU-throttle and pressure signals are only trustworthy on cgroup v2. And I stayed on Resilience4j rather than Spring Boot 4's native `@Retryable` for a specific reason — native Spring resilience has no circuit breaker and doesn't emit the metrics my validator reads. The whole project depends on resilience patterns being observable, so I chose the implementation that publishes its state to Prometheus. An unobservable circuit breaker is unverifiable, and an unverifiable pattern is exactly what this project exists to catch."*

---

## Table of Contents

**Foundation (Rev 1, re-verified)**
1. Project Overview & Interview Hook
2. Tech Stack — Every Choice Justified (exact pins in §B)
3. MVP Blueprint — 7-Week Build Plan (revised schedule in §29.4)
4. High-Level Design (HLD)
5. Low-Level Design (LLD)
6. Database Design & Choice (comparison matrix)
7. Caching & Messaging — Redis vs Kafka vs Zookeeper vs RabbitMQ
8. Design Patterns Used
9. Docker & Kubernetes Deployment
10. CI/CD — Chaos in the Pipeline
11. Monitoring — The First "Scaling Decision"
12. Interview Prep — Top Questions & Answers
13. Deployment Checklist

**New in Rev 2 — corrections**
C. **Rev 2 Corrections — the six defects, with fixes** (also amends §5, §6, §7, §8)

**New in Rev 2 — the differentiators**
14. **The Steady-State Hypothesis Engine**
15. **The Safety Plane: Blast Radius, Abort-on-Harm, Error-Budget Gating**
16. **Measurement Integrity: the Load Plane, SLI Validity, Honest Recovery Timing**
17. **Counterfactual Resilience Analysis**
18. **Cascading Failure Scenarios**
19. **Service Resilience Contracts**
20. **Resilience Regression Bisection**
21. **The DevOps/MLOps Wrapper: Scoring Epochs, Replay Evals, Flakiness Quarantine**
22. **Postmortem-as-Code and Signed Evidence Bundles**

**New in Rev 2 — cross-cutting engineering the brief asked for**
23. Resilience Patterns (of ChaosProof itself)
24. Availability & Consistency Patterns
25. Mitigation Strategies (failure-mode table)
26. Deployment Strategies
27. README Blueprint with Architecture Diagram
28. Interview Prep — Rev 2 Additions
29. Build Order, Competitive Grid, Schedule, Demo Script, Fact-Check Appendix

---

## 1. Project Overview & Interview Hook

**Project Name:** ChaosProof

**Tagline:** "Don't hope your system is resilient — prove it every day."

**One-liner:** A chaos engineering laboratory that runs daily automated fault-injection experiments on a Kubernetes microservices application using LitmusChaos — randomly killing pods, injecting network latency, filling disks, spiking CPU — and then automatically validates whether the system recovered within its defined SLOs, whether Prometheus alerts fired correctly, whether application-level resilience patterns (circuit breakers, retries, fallbacks) activated as designed, posts pass/fail results to Slack with full incident context, and tracks a composite resilience score over time on a Next.js dashboard — with the ultimate innovation of embedding chaos experiments directly into the CI/CD pipeline so that every deployment must survive pod kills and network latency injection before reaching production.

**Interview Hook (memorize this):**

> "I run chaos experiments in CI — every deployment must survive a random pod kill and a 500ms network latency injection before it reaches production. I built a chaos engineering lab with LitmusChaos on Kubernetes that runs 6 experiments daily: pod kill, network partition, network latency, disk fill, CPU spike, and container kill. After each experiment, my Python validation framework checks three things: did the application recover within the 30-second SLO? Did Prometheus alert within 60 seconds? Did the Resilience4j circuit breaker activate? Results go to Slack with pass/fail context and to a dashboard that tracks a composite resilience score — we went from 45% to 92% over 4 weeks as I hardened the system. The key differentiator: chaos runs in CI. A PR that breaks resilience — say, removing a circuit breaker — gets blocked because the pod-kill experiment fails."

**Why SRE interviewers love this:**
- **Chaos engineering** is on every SRE JD at Swiggy, PhonePe, Flipkart, Razorpay in 2026
- **SLO/SLI thinking** — you define error budgets and verify them under failure
- **Observability depth** — Prometheus alerts, application metrics, recovery time measurement
- **Application resilience** — circuit breakers, retries, fallbacks are validated, not assumed
- **Chaos in CI** — the most advanced pattern: shifting chaos LEFT into the deployment pipeline
- **Quantified resilience** — "45% → 92%" is a measurable improvement story
- **Google SRE book alignment** — error budgets, toil reduction, automated recovery validation

**Target Companies:**
- **Swiggy, PhonePe, Razorpay** — SRE teams running chaos experiments in production
- **Flipkart, Meesho** — Large K8s clusters needing resilience validation
- **Atlassian India** — Incident management + chaos engineering
- **Netflix India, Uber India** — Chaos engineering pioneers
- **Gojek, Grab (SEA offices in Bangalore)** — SRE-heavy cultures
- **Platform9, InfraCloud, Hasura** — K8s-native companies

---

## 2. Tech Stack — Every Choice Justified

### Core Stack

> **Rev 2: the exact pins live in §B** (verified 19 Aug 2026). The table below explains *why* each layer exists; §B says which version to install and what breaks if you don't. Four rows changed materially: Spring Boot 3.x → **4.0.x** with **Resilience4j 3.x** (and the `resilience4j-spring-boot4` artifact plus a non-transitive `resilience4j-micrometer` — §C.6), Python 3.12 → **3.14**, PostgreSQL 16 → **18.6**, Helm 3 → **4.2.x**. **And one row is missing entirely: there is no load generator.** That is defect D1 (§C.1) and it invalidates every SLI in this document until fixed — Rev 2 adds **k6 2.2.x with k6 Operator v1.0** as the load plane (§16.1). Rev 2 also adds a feature-flag plane (§17), a contract registry (§19), and a CEL policy engine for safety rules (§15.4).

| Layer | Technology | Why This (Interview Answer) |
|---|---|---|
| **Chaos Engine** | LitmusChaos 3.30+ (pin the exact monthly tag — see §B) | CNCF incubating project. Kubernetes-native chaos. ChaosExperiments as CRDs (Custom Resource Definitions). 50+ pre-built experiments. Supports pod kill, network chaos, disk fill, CPU stress, DNS chaos. Litmus is to chaos engineering what Prometheus is to monitoring — the K8s-native standard. |
| **Target Application** | Spring Boot 4.0.x microservices (Java 21) + Resilience4j 3.x | Enterprise standard. Resilience4j provides circuit breaker, retry, rate limiter, bulkhead — these are the patterns we're TESTING. Spring Boot Actuator exposes resilience metrics to Prometheus. |
| **Validation Framework** | Python 3.14 (custom) | Python orchestrates: trigger experiment → wait → validate recovery → check alerts → check resilience patterns → compute score → report. Uses `kubernetes` client, `prometheus-api-client`, `slack-bolt`. |
| **Monitoring** | Prometheus + Alertmanager | Core of the validation: did the alert fire? How fast? Was the SLI violated? Prometheus is the source of truth for all metrics. |
| **Visualization** | Grafana 13 + Next.js 16.3 dashboard | Grafana for raw observability (during experiments). Next.js for the custom resilience score dashboard, experiment history, and trend analysis. |
| **Dashboard UI** | Tailwind CSS + shadcn/ui + Tremor | Dark-mode SRE dashboard. Tremor for KPI cards and charts. shadcn for polished components. |
| **Database** | PostgreSQL 18.6 | See Section 6. Experiment definitions, execution results, validation checks, resilience scores — all relational. Low-frequency event data (6 experiments/day × validation results). |
| **Cache** | Valkey 9.1 / Redis 8.x | See Section 7. Dashboard cache, experiment deduplication, Prometheus query cache, experiment job queue. |
| **Notifications** | Slack Bolt SDK (Python) | Rich Block Kit messages with experiment context, pass/fail status, recovery timeline, and "investigate" deep links to Grafana. |
| **CI/CD** | GitHub Actions | Chaos experiments embedded in the deployment pipeline. PR must pass chaos gate before merge. |
| **Container Orchestration** | Kubernetes 1.36 (kind for local, EKS/GKE for prod; 1.35 is the supported floor) | ChaosProof runs ON K8s, tests ON K8s. LitmusChaos requires K8s. |
| **Package Management** | Helm 4.2.x | Charts for LitmusChaos, target app, ChaosProof platform, Prometheus stack. |
| **SLO Management** | Custom Python + Prometheus rules | SLO definitions in YAML, validated via PromQL queries after each experiment. |

### Why NOT These Alternatives

| Rejected Option | Why |
|---|---|
| Chaos Mesh (PingCAP) | Excellent tool but smaller community than LitmusChaos. Litmus has CNCF backing, more pre-built experiments, better CRD-based workflow model, and a built-in chaos center UI. For interview value, CNCF affiliation matters. |
| Gremlin (SaaS) | Commercial chaos-as-a-service. Using it means you're configuring, not building. Building the validation + scoring + CI integration layer from scratch demonstrates deep understanding. |
| AWS Fault Injection Simulator | AWS-only. Can't run on GCP or bare-metal K8s. LitmusChaos is cloud-agnostic. |
| Toxiproxy (Shopify) | Application-level network chaos only (proxy-based). LitmusChaos operates at the Kubernetes/OS level — kills pods, fills disks, injects kernel-level network faults. Broader fault coverage. |
| TimescaleDB | Experiment events are low-frequency: 6 experiments/day × ~20 validation checks = ~120 records/day. PostgreSQL handles this without time-series extensions. TimescaleDB would add complexity for zero benefit. |
| MongoDB | Experiment data is deeply relational: experiment → execution → validation_checks → alert_validations → resilience_pattern_checks → resilience_score. 6+ table JOINs. MongoDB would require deep denormalization or $lookup chains. |
| Kafka | Under 50 experiment events per day. Kafka's 3-broker minimum for 50 messages/day is absurd overengineering. Redis Streams handles the experiment job queue trivially. |
| Istio (for network chaos) | Istio can inject faults at the service mesh level but requires the entire Istio installation (~500MB, complex). LitmusChaos injects network faults at the Linux kernel level (tc/iptables) without requiring a service mesh — simpler, lower overhead. |

---

## 3. MVP Blueprint — 7-Week Build Plan

### The 6 Chaos Experiments

> **Rev 2 changes four things about this table.** (1) Every experiment now carries a **falsifiable hypothesis** with per-invariant verdicts and explicit abort conditions (§14.2), so “What Should Recover” becomes machine-checkable rather than prose. (2) Every experiment declares a **`min_rps_floor`**; below it the verdict is `INVALID`, not `PASS` (§16.1). (3) **Disk fill** asserts *pod eviction and reschedule*, not `NodeDiskPressure` — Litmus `disk-fill` consumes the container’s ephemeral storage and those are different failure modes (§C.2). (4) **CPU spike** asserts a rising throttle ratio and a holding *aggregate* SLI, not “the spike goes away” — scaling adds healthy replicas while the hogged pod stays hogged (§C.2).

| # | Experiment | LitmusChaos Type | What Breaks | What Should Recover | SLO Validation |
|---|---|---|---|---|---|
| 1 | **Pod Kill** | `pod-delete` | Randomly kills 1 pod of a service | K8s recreates pod, traffic reroutes to surviving replicas | Recovery < 30s, zero 5xx during kill |
| 2 | **Network Latency** | `pod-network-latency` | Adds 500ms latency between services | Circuit breaker opens → fallback activates → user gets degraded-but-working response | Latency SLO: P99 < 2s (degraded but within budget) |
| 3 | **Network Partition** | `pod-network-loss` | 100% packet loss between two services | Circuit breaker opens → fallback response → alert fires | Availability SLO: > 95% during partition |
| 4 | **Disk Fill** | `disk-fill` | Fills ephemeral storage to 90% | App handles gracefully (log rotation, temp cleanup) → alert fires → no crash | No pod restarts, alert within 60s |
| 5 | **CPU Spike** | `pod-cpu-hog` | Consumes 90% CPU for 60 seconds | HPA scales replicas → latency increases but stays within SLO → recovery after spike ends | Latency P99 < 5s during spike, auto-scale triggers |
| 6 | **Container Kill** | `container-kill` | Kills container (not pod) — container-runtime level (containerd; see §C.2) | Container restarts within pod → traffic continues via readiness probe | Recovery < 15s, readiness probe flips back |

### The Target Application — Resilience-Instrumented Microservices

3 Spring Boot 4 services designed with specific resilience patterns to TEST:

| Service | Resilience Patterns | Why These Patterns |
|---|---|---|
| `order-api` (gateway) | **Circuit Breaker** (to payment-svc), **Retry** (to inventory-svc), **Timeout** (global 3s) | Gateway must handle downstream failures gracefully — CB prevents cascade, retry handles transient failures |
| `payment-service` | **Bulkhead** (isolate payment processing threads), **Rate Limiter** (100 req/s), **Fallback** (queue payment for retry) | Payment is critical — bulkhead prevents thread starvation, rate limiter prevents overload, fallback queues for async processing |
| `inventory-service` | **Retry** (to database), **Circuit Breaker** (to cache), **Cache Fallback** (serve stale data on cache failure) | Read-heavy service — retries handle DB blips, cache CB prevents cascading, stale cache is acceptable fallback |

**Resilience4j Configuration (Observable Metrics):**

```yaml
# Each resilience pattern exposes Prometheus metrics:
resilience4j.circuitbreaker:
  instances:
    paymentService:
      registerHealthIndicator: true
      slidingWindowSize: 10
      failureRateThreshold: 50
      waitDurationInOpenState: 30s
      # Prometheus metrics: resilience4j_circuitbreaker_state
      #                    resilience4j_circuitbreaker_calls_total

resilience4j.retry:
  instances:
    inventoryService:
      maxAttempts: 3
      waitDuration: 500ms
      # Prometheus metrics: resilience4j_retry_calls_total

resilience4j.bulkhead:
  instances:
    paymentProcessing:
      maxConcurrentCalls: 25
      # Prometheus metrics: resilience4j_bulkhead_available_concurrent_calls
```

### SLO Definitions (What We're Validating)

> **Rev 2 warning on these SLIs.** The queries below are correct PromQL and were **unmeasurable as written**, for two reasons fixed in §16. First, they are ratios over request counters with nothing generating requests — 0/0 (§C.1). Second, `recovery_time.target_seconds: 30` cannot be resolved by a `[5m]` rate window; the metric lags the SLO by an order of magnitude (§C.1 D2, fixed in §16.3). Rev 2 keeps these targets and changes how they are measured: 5s scrape on the target app, ≥30s windows, and k6 client-side series as the primary recovery clock. Also note `alert_latency.target_seconds: 60` is **arithmetically impossible** with a `for: 1m` rule — §16.5 measures excess latency instead and lints for this in CI.

```yaml
# slos/definitions.yaml — The SRE heart of the project

slos:
  - name: availability
    description: "Percentage of successful HTTP requests"
    target: 99.5
    sli_query: |
      sum(rate(http_server_requests_seconds_count{status!~"5.."}[5m]))
      / sum(rate(http_server_requests_seconds_count[5m])) * 100
    during_chaos_target: 95.0  # Relaxed during experiments
    
  - name: latency_p99
    description: "99th percentile request latency"
    target_ms: 500
    sli_query: |
      histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket[5m])) by (le))
    during_chaos_target_ms: 2000  # Degraded but acceptable
    
  - name: recovery_time
    description: "Time from fault injection to full recovery"
    target_seconds: 30
    measurement: "time_from_experiment_start_to_sli_restoration"
    
  - name: alert_latency
    description: "Time from fault to Prometheus alert firing"
    target_seconds: 60
    measurement: "time_from_experiment_start_to_alertmanager_notification"
    
  - name: circuit_breaker_activation
    description: "Circuit breaker should open within N seconds of failure"
    target_seconds: 10
    measurement: "time_from_fault_to_resilience4j_circuitbreaker_state_change"
```

### Week-by-Week Schedule

**Week 1 — K8s + Target App + Monitoring Foundation**
- Day 1–2: K8s cluster (kind), Prometheus stack (kube-prometheus-stack Helm chart)
- Day 3–4: Build 3 Spring Boot microservices with Resilience4j, Dockerize, push images
- Day 5: Helm chart for target app with intentional inter-service dependencies
- Day 6: Verify Resilience4j metrics in Prometheus: circuit breaker state, retry counts
- Day 7: Alertmanager rules for all 6 scenarios (pod restart, high latency, etc.)
- Deliverable: Resilient app on K8s with full Prometheus + Alertmanager observability

**Week 2 — LitmusChaos Setup + First 3 Experiments**
- Day 1–2: Install LitmusChaos via Helm, verify ChaosEngine CRD
- Day 3: Experiment 1 — Pod Kill (`pod-delete`): configure, run, observe recovery
- Day 4: Experiment 2 — Network Latency (`pod-network-latency`): 500ms injection
- Day 5: Experiment 3 — Network Partition (`pod-network-loss`): 100% packet loss
- Day 6–7: Manual validation for each: did the app recover? Did alerts fire?
- Deliverable: 3 chaos experiments running, manual validation documented

**Week 3 — Remaining Experiments + Automated Validation Framework**
- Day 1: Experiment 4 — Disk Fill (`disk-fill`): fill ephemeral storage
- Day 2: Experiment 5 — CPU Spike (`pod-cpu-hog`): 90% CPU for 60s
- Day 3: Experiment 6 — Container Kill (`container-kill`): CRI-level kill (`CONTAINER_RUNTIME: containerd`)
- Day 4–5: Python validation framework: SLO checker, alert checker, resilience pattern checker
- Day 6–7: Orchestrator: trigger experiment → wait → validate → score → report
- Deliverable: All 6 experiments with automated validation

**Week 4 — Resilience Scoring + Database + Dashboard**
- Day 1–2: PostgreSQL schema, experiment result storage, validation records
- Day 3: Resilience score algorithm: weighted composite across all checks
- Day 4–5: Next.js dashboard: resilience score trend, experiment history, per-check results
- Day 6: Grafana dashboards: during-experiment observability panels
- Day 7: Redis caching for dashboard queries
- Deliverable: Resilience score tracking from 45% → improving over time

**Week 5 — Slack Integration + Chaos in CI**
- Day 1–2: Slack Bolt SDK: rich experiment result messages with Block Kit
- Day 3: "Investigate" button: deep link to Grafana time range during experiment
- Day 4–5: GitHub Actions chaos gate: run pod-kill + latency experiments on every PR
- Day 6: PR status check: PASS if app survives, FAIL if resilience is broken
- Day 7: Demo: remove circuit breaker → PR fails chaos gate → must fix before merge
- Deliverable: Chaos experiments blocking bad deployments in CI

**Week 6 — SLO Error Budget + Daily Schedule**
- Day 1–2: SLO error budget tracking: how much of the error budget did today's chaos consume?
- Day 3: Scheduled chaos: K8s CronJob runs all 6 experiments daily at 3 AM
- Day 4: Historical analysis: resilience score trend chart over 30 days
- Day 5: Experiment comparison: "Pod Kill recovery improved from 45s to 12s over 3 weeks"
- Day 6–7: Documentation, README, architecture diagram
- Deliverable: Daily automated chaos with trend analysis

**Week 7 — Polish, Helm Charts, Deploy, Demo**
- Day 1–2: Helm chart for ChaosProof platform
- Day 3: Docker Compose for local development (without K8s experiments)
- Day 4: CI/CD for ChaosProof itself
- Day 5: Deploy to cloud K8s cluster
- Day 6: Record demo video: "45% to 92% resilience improvement"
- Day 7: Final polish, publish
- Deliverable: Live deployed, fully documented, demo-ready

---

## 4. High-Level Design (HLD)

### Architecture Overview

> **Rev 2: this diagram is missing two planes.** It has no **load plane** (k6 Operator driving constant-arrival-rate traffic — without it every SLI below is 0/0, §C.1) and no **safety plane** (pre-flight blast-radius and budget gating, in-experiment abort probes, cleanup saga — §15). The complete Rev 2 architecture diagram is in §27; read this one for the validation and scoring topology, which is unchanged and correct.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      KUBERNETES CLUSTER                                  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────┐              │
│  │        TARGET APPLICATION (Resilience-Instrumented)    │              │
│  │                                                       │              │
│  │  ┌─────────────┐    ┌─────────────┐    ┌───────────┐  │              │
│  │  │ order-api   │───→│ payment-svc │    │inventory  │  │              │
│  │  │             │───→│             │    │  -svc     │  │              │
│  │  │ • CircuitBrk│    │ • Bulkhead  │    │ • Retry   │  │              │
│  │  │ • Retry     │    │ • RateLimiter│    │ • CB      │  │              │
│  │  │ • Timeout   │    │ • Fallback  │    │ • Fallback│  │              │
│  │  └─────────────┘    └─────────────┘    └───────────┘  │              │
│  │         ↑ Resilience4j metrics → Prometheus            │              │
│  └─────────┼─────────────────────────────────────────────┘              │
│            │                                                            │
│  ┌─────────┼─────────────────────────────────────────────┐              │
│  │         │    OBSERVABILITY STACK                        │              │
│  │  ┌──────▼──────┐  ┌──────────────┐  ┌──────────────┐  │              │
│  │  │ Prometheus  │  │ Alertmanager │  │  Grafana     │  │              │
│  │  │             │→ │              │  │              │  │              │
│  │  │ • App SLIs  │  │ • Recovery   │  │ • Real-time  │  │              │
│  │  │ • K8s state │  │   alerts     │  │   during-    │  │              │
│  │  │ • R4j state │  │ • Latency    │  │   experiment │  │              │
│  │  │ • Node      │  │   alerts     │  │   dashboards │  │              │
│  │  └─────────────┘  └──────┬───────┘  └──────────────┘  │              │
│  └──────────────────────────┼────────────────────────────┘              │
│                             │ alert webhook                              │
│  ┌──────────────────────────▼────────────────────────────┐              │
│  │        CHAOSPR00F PLATFORM                             │              │
│  │                                                       │              │
│  │  ┌───────────────────────────────────────────────────┐ │              │
│  │  │  LITMUS CHAOS ENGINE                               │ │              │
│  │  │  (Chaos Experiments as K8s CRDs)                   │ │              │
│  │  │                                                   │ │              │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐          │ │              │
│  │  │  │Pod Kill  │ │Net Latency│ │Net Loss  │          │ │              │
│  │  │  │(pod-     │ │(pod-net- │ │(pod-net- │          │ │              │
│  │  │  │ delete)  │ │ latency) │ │ loss)    │          │ │              │
│  │  │  └──────────┘ └──────────┘ └──────────┘          │ │              │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐          │ │              │
│  │  │  │Disk Fill │ │CPU Spike │ │Container │          │ │              │
│  │  │  │(disk-    │ │(pod-cpu- │ │Kill      │          │ │              │
│  │  │  │ fill)    │ │ hog)     │ │(container│          │ │              │
│  │  │  │          │ │          │ │ -kill)   │          │ │              │
│  │  │  └──────────┘ └──────────┘ └──────────┘          │ │              │
│  │  └───────────────────────────────────────────────────┘ │              │
│  │                                                       │              │
│  │  ┌───────────────────────────────────────────────────┐ │              │
│  │  │  PYTHON VALIDATION FRAMEWORK                       │ │              │
│  │  │                                                   │ │              │
│  │  │  ┌───────────────┐ ┌─────────────┐ ┌───────────┐  │ │              │
│  │  │  │ Experiment    │ │ SLO         │ │ Resilience│  │ │              │
│  │  │  │ Orchestrator  │→│ Validator   │→│ Pattern   │  │ │              │
│  │  │  │               │ │             │ │ Checker   │  │ │              │
│  │  │  │ trigger →     │ │ • Recovery  │ │ • CB open?│  │ │              │
│  │  │  │ wait →        │ │   time      │ │ • Retry   │  │ │              │
│  │  │  │ validate →    │ │ • Avail SLO │ │   fired?  │  │ │              │
│  │  │  │ score →       │ │ • Latency   │ │ • Fallback│  │ │              │
│  │  │  │ report        │ │   SLO       │ │   used?   │  │ │              │
│  │  │  └───────────────┘ │ • Alert     │ │ • Bulkhead│  │ │              │
│  │  │                    │   fired?    │ │   active? │  │ │              │
│  │  │  ┌───────────────┐ └─────────────┘ └───────────┘  │ │              │
│  │  │  │ Score         │                                │ │              │
│  │  │  │ Calculator    │ ← computes composite score     │ │              │
│  │  │  └──────┬────────┘                                │ │              │
│  │  └─────────┼─────────────────────────────────────────┘ │              │
│  │            │                                           │              │
│  │  ┌─────────▼─────────┐  ┌────────────┐  ┌──────────┐  │              │
│  │  │  PostgreSQL 18    │  │  Valkey 9  │  │  Next.js │  │              │
│  │  │  (results +       │  │  (cache +  │  │Dashboard │  │              │
│  │  │   scores +        │  │   queue)   │  │(resilience│  │              │
│  │  │   history)        │  │            │  │ score +   │  │              │
│  │  └───────────────────┘  └────────────┘  │ trends)  │  │              │
│  │                                         └──────────┘  │              │
│  └───────────────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────────┘
                        │
                        ▼
              ┌──────────────────┐          ┌──────────────────┐
              │   Slack          │          │  GitHub Actions   │
              │                  │          │  (Chaos in CI)    │
              │  • Pass/Fail     │          │                  │
              │  • Recovery time │          │  • PR chaos gate │
              │  • Investigate   │          │  • Daily schedule│
              │    (Grafana link)│          │                  │
              └──────────────────┘          └──────────────────┘
```

### Core Experiment Flow

> **Rev 2 inserts three stages ahead of Step 1 and one during Step 3.** Before baseline: **start the load plane and wait for steady state**, then run **pre-flight** — steady-state check, SLI validity floor, blast-radius computation, and the error-budget gate, any of which can return `SKIPPED` or `DENIED` (§C.5, §15). During the fault: **continuous Litmus probes with `stopOnFailure` plus an independent watchdog** can abort mid-experiment, yielding `ABORTED` and running the cleanup saga (§15.3, §15.6). The full Rev 2 runner is in §14.4.

```
DAILY SCHEDULE (K8s CronJob at 3 AM) OR CI TRIGGER (GitHub Actions on PR)
    │
    ├── For each experiment in [pod_kill, net_latency, net_partition,
    │                           disk_fill, cpu_spike, container_kill]:
    │
    │   Step 1: PRE-EXPERIMENT BASELINE
    │           → Query Prometheus: current availability SLI, latency P99
    │           → Record: circuit breaker state (CLOSED), retry count, pod count
    │           → Timestamp: experiment_start_time
    │
    │   Step 2: INJECT FAULT
    │           → Apply LitmusChaos ChaosEngine CRD to K8s
    │           → Litmus runner pod starts → injects fault
    │           → Fault active for configured duration (30-120 seconds)
    │
    │   Step 3: OBSERVE (during fault)
    │           → Poll Prometheus every 5 seconds:
    │             • HTTP error rate (5xx)
    │             • Latency P50, P99
    │             • Circuit breaker state changes
    │             • Pod restarts
    │             • HPA scaling events
    │
    │   Step 4: FAULT ENDS (Litmus experiment completes)
    │           → Record: fault_end_time
    │           → Begin recovery observation window (120 seconds)
    │
    │   Step 5: VALIDATE RECOVERY
    │           → Check 1: SLO Recovery
    │             "Did availability return to ≥ 99.5% within 30 seconds?"
    │             PromQL: rate(http_server_requests_seconds_count{status!~"5.."}[1m])
    │             PASS if restored within target, FAIL if not
    │
    │           → Check 2: Alert Validation
    │             "Did Prometheus fire an alert within 60 seconds of fault start?"
    │             Query Alertmanager API: /api/v2/alerts?filter=...
    │             PASS if alert found with correct labels, FAIL if no alert
    │
    │           → Check 3: Resilience Pattern Activation
    │             "Did the circuit breaker open when downstream failed?"
    │             PromQL: resilience4j_circuitbreaker_state{name="paymentService"} == 1
    │             PASS if state changed CLOSED→OPEN→HALF_OPEN→CLOSED, FAIL if stayed CLOSED
    │
    │           → Check 4: Recovery Completeness
    │             "Is the system fully healthy now?"
    │             All pods Running, no CrashLoopBackOff, latency below SLO
    │             PASS if clean, FAIL if lingering issues
    │
    │   Step 6: COMPUTE SCORE
    │           → Each check: PASS = 1.0, PARTIAL = 0.5, FAIL = 0.0
    │           → Experiment score = weighted average of checks
    │           → Daily resilience score = average across all experiments
    │
    │   Step 7: PERSIST + NOTIFY
    │           → Store in PostgreSQL: execution record + all validation checks
    │           → Update resilience score history
    │           → Post to Slack: pass/fail per experiment with context
    │           → Cache latest results in Redis for dashboard
    │
    └── After all experiments:
        → Compute daily aggregate resilience score
        → Update dashboard trend chart
        → If in CI: set PR status check (PASS/FAIL)
```

### Resilience Score Calculation

> **Rev 2 corrects this formula — see §C.4.** The worked example below scores Disk Fill at 0.625 with `pattern=N/A`, which is only reachable by treating the non-applicable check as **0.5 partial credit**. That silently rewards experiments with no resilience pattern to validate. Rev 2 marks checks `applicable` and **renormalises the weights** over applicable checks only (Disk Fill becomes 0.588), and adds a third outcome — `INVALID` — which produces **no score at all** (§16.1). Scores are also stamped with a **scoring epoch** so a trend line never silently spans a change to these weights (§21.1).

```
DAILY RESILIENCE SCORE (0-100)

For each experiment E:
  recovery_score      = 1.0 if recovered within SLO, 0.0 if not, 0.5 if partial
  alert_score         = 1.0 if alert fired on time, 0.0 if missed
  pattern_score       = 1.0 if resilience pattern activated correctly, 0.0 if not
  completeness_score  = 1.0 if fully recovered, 0.0 if lingering issues

  experiment_score(E) = (
    recovery_score * 0.35     +  # Recovery time is most important
    alert_score * 0.25        +  # Alerting must work
    pattern_score * 0.25      +  # Application resilience must activate
    completeness_score * 0.15    # Clean recovery matters
  )

DAILY SCORE = AVG(experiment_score(E) for all E) × 100

Example:
  Pod Kill:         recovery=1.0, alert=1.0, pattern=1.0, complete=1.0 → 1.0
  Network Latency:  recovery=1.0, alert=1.0, pattern=0.5, complete=1.0 → 0.875
  Network Partition: recovery=0.5, alert=1.0, pattern=1.0, complete=0.5 → 0.750
  Disk Fill:        recovery=1.0, alert=0.0, pattern=N/A,  complete=1.0 → 0.625
  CPU Spike:        recovery=1.0, alert=1.0, pattern=1.0, complete=1.0 → 1.0
  Container Kill:   recovery=1.0, alert=1.0, pattern=1.0, complete=1.0 → 1.0

  Daily Score = AVG(1.0, 0.875, 0.750, 0.625, 1.0, 1.0) × 100 = 87.5%

  [ARITHMETIC CORRECTED 26 Aug 2026 by evals/scoring_worked_example.py.
   Network Partition published as 0.725; the weights give
   0.35(0.5) + 0.25(1.0) + 0.25(1.0) + 0.15(0.5) = 0.750, moving the
   daily aggregate from the published 87.1% to 87.5%.]
```

---

## 5. Low-Level Design (LLD)

### 5.1 Project Structure

```
chaosproof/
├── target-app/                              # Spring Boot microservices
│   ├── order-api/
│   │   ├── src/main/java/
│   │   │   └── com/chaosproof/orderapi/
│   │   │       ├── OrderApiApplication.java
│   │   │       ├── controller/OrderController.java
│   │   │       ├── service/OrderService.java
│   │   │       ├── resilience/
│   │   │       │   ├── PaymentCircuitBreaker.java
│   │   │       │   ├── InventoryRetry.java
│   │   │       │   └── FallbackHandlers.java
│   │   │       └── config/Resilience4jConfig.java
│   │   ├── src/main/resources/application.yml  # Resilience4j + Prometheus config
│   │   ├── Dockerfile
│   │   └── pom.xml
│   ├── payment-service/
│   │   ├── src/main/java/.../
│   │   │   ├── resilience/
│   │   │   │   ├── ProcessingBulkhead.java
│   │   │   │   ├── PaymentRateLimiter.java
│   │   │   │   └── QueueFallback.java
│   │   ├── Dockerfile
│   │   └── pom.xml
│   └── inventory-service/
│       ├── src/main/java/.../
│       │   ├── resilience/
│       │   │   ├── DatabaseRetry.java
│       │   │   ├── CacheCircuitBreaker.java
│       │   │   └── StaleCacheFallback.java
│       ├── Dockerfile
│       └── pom.xml
│
├── chaos-framework/                         # Python validation + orchestration
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                          # CLI + scheduler entrypoint
│   │   ├── config.py                        # Environment configuration
│   │   │
│   │   ├── orchestrator/
│   │   │   ├── experiment_runner.py          # Runs one experiment end-to-end
│   │   │   ├── daily_scheduler.py            # Runs all experiments in sequence
│   │   │   └── ci_runner.py                  # Subset of experiments for CI gate
│   │   │
│   │   ├── experiments/
│   │   │   ├── base.py                       # Abstract ChaosExperiment class
│   │   │   ├── pod_kill.py                   # Pod delete experiment
│   │   │   ├── network_latency.py            # 500ms latency injection
│   │   │   ├── network_partition.py          # 100% packet loss
│   │   │   ├── disk_fill.py                  # Ephemeral storage fill
│   │   │   ├── cpu_spike.py                  # CPU hog experiment
│   │   │   └── container_kill.py             # Docker-level container kill
│   │   │
│   │   ├── validators/
│   │   │   ├── base.py                       # Abstract ValidationCheck
│   │   │   ├── slo_validator.py              # Check SLI against SLO targets
│   │   │   ├── alert_validator.py            # Verify Prometheus alerts fired
│   │   │   ├── resilience_pattern_validator.py  # Check CB, retry, bulkhead
│   │   │   ├── recovery_validator.py         # Verify full system recovery
│   │   │   └── slo_definitions.py            # Load SLO YAML definitions
│   │   │
│   │   ├── scoring/
│   │   │   ├── score_calculator.py           # Composite resilience score
│   │   │   └── trend_analyzer.py             # Historical trend computation
│   │   │
│   │   ├── integrations/
│   │   │   ├── prometheus_client.py          # PromQL queries
│   │   │   ├── alertmanager_client.py        # Alert query API
│   │   │   ├── kubernetes_client.py          # K8s API for CRD management
│   │   │   ├── litmus_client.py              # Apply/monitor ChaosEngine CRDs
│   │   │   └── slack_reporter.py             # Slack Block Kit notifications
│   │   │
│   │   ├── db/
│   │   │   ├── connection.py                 # SQLAlchemy async engine
│   │   │   ├── models.py                     # ORM models
│   │   │   └── queries.py
│   │   │
│   │   └── cache/
│   │       ├── redis_client.py
│   │       └── experiment_cache.py
│   │
│   ├── slos/
│   │   └── definitions.yaml                  # SLO/SLI definitions
│   │
│   ├── tests/
│   │   ├── test_slo_validator.py
│   │   ├── test_score_calculator.py
│   │   ├── test_alert_validator.py
│   │   ├── test_resilience_pattern_validator.py
│   │   └── fixtures/
│   │       ├── prometheus_responses/
│   │       └── alertmanager_responses/
│   ├── Dockerfile
│   └── requirements.txt
│
├── dashboard/                               # Next.js resilience dashboard
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx                     # Main: resilience score + experiments
│   │   │   ├── experiments/
│   │   │   │   └── [id]/page.tsx            # Experiment execution detail
│   │   │   ├── trends/page.tsx              # Historical resilience trends
│   │   │   ├── slos/page.tsx                # SLO definitions + error budget
│   │   │   └── api/
│   │   │       ├── score/route.ts
│   │   │       ├── experiments/route.ts
│   │   │       ├── trends/route.ts
│   │   │       └── health/route.ts
│   │   ├── components/
│   │   │   ├── dashboard/
│   │   │   │   ├── ResilienceGauge.tsx       # Large gauge: current score (0-100)
│   │   │   │   ├── ExperimentCards.tsx       # 6 cards: one per experiment type
│   │   │   │   ├── ScoreTrendChart.tsx       # 30-day resilience score line chart
│   │   │   │   ├── ValidationChecklist.tsx   # Per-experiment: ✅/❌ per check
│   │   │   │   ├── RecoveryTimeline.tsx      # Timeline: fault→detect→alert→recover
│   │   │   │   └── ErrorBudgetBurn.tsx       # SLO error budget consumption
│   │   │   └── common/
│   │   │       ├── StatusBadge.tsx
│   │   │       └── LoadingSkeleton.tsx
│   │   └── types/index.ts
│   ├── Dockerfile
│   └── package.json
│
├── litmus-experiments/                      # ChaosEngine CRD definitions
│   ├── pod-kill.yaml
│   ├── network-latency.yaml
│   ├── network-partition.yaml
│   ├── disk-fill.yaml
│   ├── cpu-spike.yaml
│   └── container-kill.yaml
│
├── charts/
│   ├── chaosproof/                          # Main platform chart
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   │       ├── framework-deployment.yaml
│   │       ├── dashboard-deployment.yaml
│   │       ├── scheduler-cronjob.yaml       # Daily 3 AM chaos
│   │       ├── postgres-statefulset.yaml
│   │       ├── redis-deployment.yaml
│   │       ├── rbac.yaml                    # ServiceAccount for K8s + Litmus
│   │       └── configmap.yaml
│   ├── target-app/                          # Target microservices chart
│   └── litmus/                              # LitmusChaos chart values
│
├── monitoring/
│   ├── alerting-rules.yml                   # Prometheus alert rules
│   └── grafana/
│       ├── during-experiment-dashboard.json  # Real-time experiment view
│       └── chaosproof-operational.json       # Self-monitoring
│
├── .github/workflows/
│   ├── ci.yml                               # Lint + test
│   ├── chaos-gate.yml                       # Chaos in CI (PR gate)
│   ├── deploy.yml                           # Deploy to K8s
│   └── daily-chaos.yml                      # Scheduled daily experiments
│
├── docker-compose.yml
├── Makefile
└── README.md
```

### 5.2 Core: Experiment Orchestrator

> ⚠️ **Rev 2 replaces this runner — see §14.4 for the complete file.** The version below has no load plane, no pre-flight gate, no abort path, and its `_capture_baseline` / `_observe_during_fault` queries use `[5m]` and `[1m]` windows against a 30-second recovery SLO (§C.1 D1, D2). Its sampling loop also drifts, because `asyncio.sleep(5)` follows sequential awaits — use a fixed-deadline scheduler and record actual sample timestamps (§C.2). Read the structure below for orientation, then implement §14.4.

```python
# chaos-framework/src/orchestrator/experiment_runner.py

import asyncio
import time
from dataclasses import dataclass
from typing import List

from ..experiments.base import ChaosExperiment
from ..validators.slo_validator import SLOValidator
from ..validators.alert_validator import AlertValidator
from ..validators.resilience_pattern_validator import ResiliencePatternValidator
from ..validators.recovery_validator import RecoveryValidator
from ..scoring.score_calculator import ScoreCalculator
from ..integrations.litmus_client import LitmusClient
from ..integrations.prometheus_client import PrometheusClient
from ..integrations.slack_reporter import SlackReporter
from ..db.models import ExperimentExecution, ValidationCheck


@dataclass
class ExperimentResult:
    experiment_name: str
    status: str                    # passed, failed, partial
    score: float                   # 0.0 - 1.0
    recovery_time_seconds: float
    checks: List[dict]
    timeline: List[dict]           # Event timeline for visualization
    metadata: dict


class ExperimentRunner:
    """Orchestrates a single chaos experiment end-to-end."""

    def __init__(self):
        self.litmus = LitmusClient()
        self.prometheus = PrometheusClient()
        self.slo_validator = SLOValidator()
        self.alert_validator = AlertValidator()
        self.pattern_validator = ResiliencePatternValidator()
        self.recovery_validator = RecoveryValidator()
        self.scorer = ScoreCalculator()
        self.slack = SlackReporter()

    async def run(self, experiment: ChaosExperiment) -> ExperimentResult:
        timeline = []
        checks = []

        # ===== PHASE 1: BASELINE =====
        t_start = time.time()
        timeline.append({"event": "baseline_capture", "time": 0})

        baseline = await self._capture_baseline(experiment)
        
        # ===== PHASE 2: INJECT FAULT =====
        timeline.append({"event": "fault_injected", "time": time.time() - t_start})

        chaos_result = await self.litmus.apply_experiment(
            experiment.to_chaos_engine_crd()
        )

        # ===== PHASE 3: OBSERVE (during fault) =====
        observations = await self._observe_during_fault(
            experiment, duration=experiment.fault_duration_seconds
        )
        timeline.append({"event": "fault_ended", "time": time.time() - t_start})

        # ===== PHASE 4: RECOVERY WINDOW =====
        await asyncio.sleep(experiment.recovery_window_seconds)
        timeline.append({"event": "recovery_window_ended", "time": time.time() - t_start})

        # ===== PHASE 5: VALIDATE =====

        # Check 1: SLO Recovery
        slo_check = await self.slo_validator.validate(
            experiment=experiment,
            baseline=baseline,
            fault_start=t_start,
            fault_end=t_start + experiment.fault_duration_seconds
        )
        checks.append(slo_check.to_dict())
        if slo_check.recovery_time:
            timeline.append({
                "event": "slo_recovered",
                "time": slo_check.recovery_time - t_start
            })

        # Check 2: Alert Fired
        alert_check = await self.alert_validator.validate(
            experiment=experiment,
            fault_start=t_start,
            expected_alerts=experiment.expected_alerts
        )
        checks.append(alert_check.to_dict())
        if alert_check.alert_fired_at:
            timeline.append({
                "event": "alert_fired",
                "time": alert_check.alert_fired_at - t_start
            })

        # Check 3: Resilience Pattern Activation
        if experiment.expected_patterns:
            pattern_check = await self.pattern_validator.validate(
                experiment=experiment,
                fault_start=t_start,
                expected_patterns=experiment.expected_patterns
            )
            checks.append(pattern_check.to_dict())
            for pattern_event in pattern_check.activations:
                timeline.append({
                    "event": f"pattern_{pattern_event['name']}",
                    "time": pattern_event['timestamp'] - t_start
                })

        # Check 4: Full Recovery
        recovery_check = await self.recovery_validator.validate(
            experiment=experiment,
            namespace=experiment.target_namespace
        )
        checks.append(recovery_check.to_dict())

        # ===== PHASE 6: SCORE =====
        score = self.scorer.calculate(checks)
        recovery_time = slo_check.recovery_time - t_start if slo_check.recovery_time else None

        result = ExperimentResult(
            experiment_name=experiment.name,
            status="passed" if score >= 0.8 else "failed" if score < 0.5 else "partial",
            score=score,
            recovery_time_seconds=recovery_time,
            checks=checks,
            timeline=sorted(timeline, key=lambda x: x['time']),
            metadata={
                "chaos_result": chaos_result,
                "observations": observations,
                "baseline": baseline,
            }
        )

        # ===== PHASE 7: PERSIST + NOTIFY =====
        await self._persist(result)
        await self.slack.report(result)

        return result

    async def _capture_baseline(self, experiment: ChaosExperiment) -> dict:
        """Capture current SLI values before fault injection."""
        return {
            "availability": await self.prometheus.query_instant(
                'sum(rate(http_server_requests_seconds_count{status!~"5.."}[5m]))'
                '/ sum(rate(http_server_requests_seconds_count[5m])) * 100'
            ),
            "latency_p99": await self.prometheus.query_instant(
                'histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket[5m])) by (le))'
            ),
            "circuit_breaker_state": await self.prometheus.query_instant(
                'resilience4j_circuitbreaker_state{application="order-api"}'
            ),
            "pod_count": await self.prometheus.query_instant(
                f'count(kube_pod_status_phase{{namespace="{experiment.target_namespace}",phase="Running"}})'
            ),
        }

    async def _observe_during_fault(self, experiment, duration: int) -> list:
        """Poll metrics every 5 seconds during fault duration."""
        observations = []
        end_time = time.time() + duration
        while time.time() < end_time:
            obs = {
                "timestamp": time.time(),
                "availability": await self.prometheus.query_instant(
                    'sum(rate(http_server_requests_seconds_count{status!~"5.."}[1m]))'
                    '/ sum(rate(http_server_requests_seconds_count[1m])) * 100'
                ),
                "latency_p99_ms": await self.prometheus.query_instant(
                    'histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket[1m])) by (le)) * 1000'
                ),
                "error_rate": await self.prometheus.query_instant(
                    'sum(rate(http_server_requests_seconds_count{status=~"5.."}[1m]))'
                ),
            }
            observations.append(obs)
            await asyncio.sleep(5)
        return observations
```

### 5.3 LitmusChaos CRD: Pod Kill Experiment

```yaml
# litmus-experiments/pod-kill.yaml

apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: pod-kill-experiment
  namespace: target-app
spec:
  appinfo:
    appns: target-app
    applabel: "app=order-api"
    appkind: deployment
  chaosServiceAccount: litmus-admin
  experiments:
    - name: pod-delete
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: "30"            # Kill for 30 seconds
            - name: CHAOS_INTERVAL
              value: "10"            # Kill every 10 seconds
            - name: FORCE
              value: "true"          # Force delete (no graceful termination)
            - name: PODS_AFFECTED_PERC
              value: "50"            # Kill 50% of pods
        probe:
          - name: "availability-check"
            type: "httpProbe"
            httpProbe/inputs:
              url: "http://order-api.target-app:8080/health"
              method:
                get:
                  criteria: "=="
                  responseCode: "200"
            mode: "Continuous"
            runProperties:
              probeTimeout: 5
              interval: 5
              retry: 2
```

### 5.4 Alert Validator

```python
# chaos-framework/src/validators/alert_validator.py

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AlertValidationResult:
    check_name: str
    passed: bool
    score: float            # 1.0, 0.5, or 0.0
    alert_fired_at: Optional[float]
    alert_latency_seconds: Optional[float]
    expected_alerts: List[str]
    found_alerts: List[dict]
    message: str

    def to_dict(self): return self.__dict__


class AlertValidator:
    """Validates that Prometheus alerts fire correctly during chaos."""

    def __init__(self):
        self.alertmanager_url = config.ALERTMANAGER_URL

    async def validate(
        self,
        experiment,
        fault_start: float,
        expected_alerts: List[str]
    ) -> AlertValidationResult:

        found_alerts = []
        earliest_alert_time = None

        for alert_name in expected_alerts:
            # Query Alertmanager for alerts that fired during the experiment
            alerts = await self._query_alerts(
                alert_name=alert_name,
                start_time=fault_start,
                end_time=fault_start + 300  # Look within 5 minutes
            )

            if alerts:
                alert_time = min(a['startsAt_unix'] for a in alerts)
                found_alerts.extend(alerts)
                if earliest_alert_time is None or alert_time < earliest_alert_time:
                    earliest_alert_time = alert_time

        # Compute score
        alerts_found_count = len(set(a['labels']['alertname'] for a in found_alerts))
        alerts_expected_count = len(expected_alerts)

        if alerts_found_count == alerts_expected_count:
            # All expected alerts fired
            alert_latency = earliest_alert_time - fault_start if earliest_alert_time else None
            
            if alert_latency and alert_latency <= experiment.slo_alert_latency_seconds:
                score = 1.0
                message = f"All {alerts_expected_count} alerts fired within {alert_latency:.1f}s (SLO: {experiment.slo_alert_latency_seconds}s)"
            else:
                score = 0.5
                message = f"Alerts fired but took {alert_latency:.1f}s (SLO: {experiment.slo_alert_latency_seconds}s) — too slow"
        elif alerts_found_count > 0:
            score = 0.5
            message = f"Only {alerts_found_count}/{alerts_expected_count} expected alerts fired"
        else:
            score = 0.0
            message = f"No alerts fired. Expected: {expected_alerts}"

        return AlertValidationResult(
            check_name="alert_validation",
            passed=score >= 0.8,
            score=score,
            alert_fired_at=earliest_alert_time,
            alert_latency_seconds=(earliest_alert_time - fault_start) if earliest_alert_time else None,
            expected_alerts=expected_alerts,
            found_alerts=found_alerts,
            message=message,
        )

    async def _query_alerts(self, alert_name: str, start_time: float, end_time: float) -> list:
        """Query Alertmanager API for specific alerts within a time window."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            params = {
                'filter': f'alertname="{alert_name}"',
                'active': 'true',
                'silenced': 'false',
            }
            async with session.get(f"{self.alertmanager_url}/api/v2/alerts", params=params) as resp:
                alerts = await resp.json()
                return [a for a in alerts
                        if start_time <= self._parse_time(a['startsAt']) <= end_time]
```

### 5.5 Resilience Pattern Validator

```python
# chaos-framework/src/validators/resilience_pattern_validator.py

class ResiliencePatternValidator:
    """Validates that application resilience patterns (CB, retry, bulkhead) activated."""

    PATTERN_QUERIES = {
        "circuit_breaker_opened": {
            "query": 'resilience4j_circuitbreaker_state{name="{name}"} == 1',  # 1 = OPEN
            "description": "Circuit breaker should transition to OPEN state",
        },
        "circuit_breaker_recovered": {
            "query": 'resilience4j_circuitbreaker_state{name="{name}"} == 0',  # 0 = CLOSED
            "description": "Circuit breaker should recover to CLOSED state",
        },
        "retry_attempts": {
            "query": 'increase(resilience4j_retry_calls_total{name="{name}",kind="successful_with_retry"}[5m]) > 0',
            "description": "Retry mechanism should have triggered",
        },
        "bulkhead_rejected": {
            "query": 'increase(resilience4j_bulkhead_calls_total{name="{name}",kind="rejected"}[5m]) > 0',
            "description": "Bulkhead should have rejected overflow calls",
        },
        "fallback_activated": {
            "query": 'increase(resilience4j_circuitbreaker_calls_total{name="{name}",kind="failed"}[5m]) > 0',
            "description": "Fallback should have been invoked",
        },
    }

    async def validate(self, experiment, fault_start, expected_patterns: list):
        activations = []
        all_passed = True

        for pattern in expected_patterns:
            query_template = self.PATTERN_QUERIES[pattern["type"]]["query"]
            query = query_template.replace("{name}", pattern["instance_name"])

            # Query Prometheus for pattern activation
            result = await self.prometheus.query_range(
                query=query,
                start=fault_start,
                end=fault_start + experiment.fault_duration_seconds + 120,
                step="5s"
            )

            if self._pattern_activated(result):
                activation_time = self._first_activation_time(result)
                activations.append({
                    "name": pattern["type"],
                    "instance": pattern["instance_name"],
                    "timestamp": activation_time,
                    "latency_seconds": activation_time - fault_start,
                    "passed": True,
                })
            else:
                all_passed = False
                activations.append({
                    "name": pattern["type"],
                    "instance": pattern["instance_name"],
                    "passed": False,
                    "message": f"{pattern['type']} did NOT activate for {pattern['instance_name']}",
                })

        passed_count = sum(1 for a in activations if a['passed'])
        total_count = len(activations)
        score = passed_count / total_count if total_count > 0 else 0

        return PatternValidationResult(
            check_name="resilience_pattern_validation",
            passed=all_passed,
            score=score,
            activations=activations,
            message=f"{passed_count}/{total_count} resilience patterns activated correctly",
        )
```

### 5.6 Key API Endpoints (Dashboard)

```
RESILIENCE SCORE
  GET    /api/score/current              # Latest resilience score (0-100)
  GET    /api/score/trend                # 30-day score history
         ?days=30

EXPERIMENTS
  GET    /api/experiments                # List all experiment types
  GET    /api/experiments/latest          # Most recent execution per type
  GET    /api/experiments/executions      # All executions (paginated)
         ?type=pod_kill&status=failed
  GET    /api/experiments/executions/:id  # Full detail: checks, timeline, observations

SLO
  GET    /api/slos                       # SLO definitions + current status
  GET    /api/slos/error-budget          # Error budget remaining per SLO

HEALTH
  GET    /api/health
```

---

## 6. Database Design & Choice

### 6.1 Comparison Matrix

| Criteria | PostgreSQL 18.6 | TimescaleDB | MongoDB | Cassandra |
|---|---|---|---|---|
| **Event Rate** | 6 experiments/day × ~20 checks = ~120 records/day. Trivial for Postgres | Designed for millions/sec — overkill | Works but no relational JOINs | Absurd |
| **Core Query** | "Show all checks for pod-kill experiment #47 with SLO definitions" = 3 JOINs | Same (is Postgres) | $lookup chains | Not designed for analytical JOINs |
| **Score Trend** | GROUP BY date → 30 rows for 30 days | Continuous aggregates (overkill for 30 rows) | Aggregation pipeline | Not suited |
| **Audit Integrity** | ACID: experiment record + all checks + score in one transaction | Same | Single-doc ACID | Eventual consistency |
| **JSONB** | Stores observations, Prometheus responses, Litmus CRD outputs | Same | Native | No |

### VERDICT: PostgreSQL 18.6 (plain — NOT TimescaleDB)

> **Rev 2 amends this section in §C.7:** the matrix gains the **CosmosDB** and **generic NoSQL** rows the brief asked for, the verdict is unchanged but the version moves to 18.6, and eight new tables are added (hypotheses, hypothesis_results, load_runs, sli_samples, scoring_epochs, contracts, counterfactual_runs, flakiness). §C.7 also states the honest threshold at which TimescaleDB *would* win, because `sli_samples` is genuinely time-series data.

**Why:** Experiment data is deeply relational (experiment → execution → validation_checks → pattern_activations → score), low-frequency (~120 records/day), and requires ACID integrity (an execution with partial checks is meaningless). Score trend queries return 30 rows for a 30-day chart — PostgreSQL handles this in < 1ms.

### 6.2 Schema Design

```sql
-- Experiment type definitions
CREATE TABLE experiment_types (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) UNIQUE NOT NULL,    -- pod_kill, network_latency, etc.
    litmus_type     VARCHAR(100) NOT NULL,           -- pod-delete, pod-network-latency, etc.
    description     TEXT,
    fault_duration_seconds INT DEFAULT 30,
    recovery_window_seconds INT DEFAULT 120,
    expected_alerts JSONB DEFAULT '[]',              -- ["KubePodCrashLooping"]
    expected_patterns JSONB DEFAULT '[]',            -- [{"type":"circuit_breaker_opened","instance_name":"paymentService"}]
    slo_recovery_seconds INT DEFAULT 30,
    slo_alert_latency_seconds INT DEFAULT 60,
    is_ci_gate      BOOLEAN DEFAULT FALSE,           -- Run in CI pipeline?
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Individual experiment executions
CREATE TABLE experiment_executions (
    id                  BIGSERIAL PRIMARY KEY,
    experiment_type_id  INT REFERENCES experiment_types(id),
    trigger_source      VARCHAR(30) NOT NULL,          -- daily_schedule, ci_pipeline, manual
    github_pr           INT,                           -- PR number if triggered from CI
    github_run_id       BIGINT,
    
    status              VARCHAR(20) NOT NULL,           -- running, passed, failed, partial, error
    score               DECIMAL(4,3),                   -- 0.000 - 1.000
    recovery_time_seconds DECIMAL(6,2),
    
    baseline_metrics    JSONB,                          -- Pre-experiment SLI values
    observations        JSONB DEFAULT '[]',             -- During-experiment metric samples
    timeline            JSONB DEFAULT '[]',             -- Event timeline for visualization
    litmus_result       JSONB,                          -- Raw Litmus ChaosResult output
    
    started_at          TIMESTAMP DEFAULT NOW(),
    fault_injected_at   TIMESTAMP,
    fault_ended_at      TIMESTAMP,
    validation_completed_at TIMESTAMP,
    
    error_message       TEXT                            -- If status = 'error'
);
CREATE INDEX idx_exec_type ON experiment_executions(experiment_type_id, started_at DESC);
CREATE INDEX idx_exec_status ON experiment_executions(status);
CREATE INDEX idx_exec_trigger ON experiment_executions(trigger_source);

-- Validation checks (per execution)
CREATE TABLE validation_checks (
    id                  BIGSERIAL PRIMARY KEY,
    execution_id        BIGINT REFERENCES experiment_executions(id) ON DELETE CASCADE,
    check_type          VARCHAR(50) NOT NULL,           -- slo_recovery, alert_validation,
                                                        -- resilience_pattern, recovery_completeness
    check_name          VARCHAR(100) NOT NULL,
    passed              BOOLEAN NOT NULL,
    score               DECIMAL(4,3),                   -- 0.0, 0.5, or 1.0
    
    -- Type-specific details
    expected_value      VARCHAR(200),                   -- "recovery < 30s"
    actual_value        VARCHAR(200),                   -- "recovered in 12.4s"
    message             TEXT,                           -- Human-readable explanation
    details             JSONB DEFAULT '{}',             -- Full validation context
    
    validated_at        TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_check_exec ON validation_checks(execution_id);

-- Daily resilience scores (aggregate)
CREATE TABLE daily_resilience_scores (
    id                  SERIAL PRIMARY KEY,
    date                DATE UNIQUE NOT NULL,
    score               DECIMAL(5,2) NOT NULL,          -- 0.00 - 100.00
    experiments_run     INT DEFAULT 0,
    experiments_passed  INT DEFAULT 0,
    experiments_failed  INT DEFAULT 0,
    avg_recovery_seconds DECIMAL(6,2),
    details             JSONB DEFAULT '{}',              -- Per-experiment breakdown
    computed_at         TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_score_date ON daily_resilience_scores(date DESC);

-- SLO definitions and tracking
CREATE TABLE slo_definitions (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL,
    description     TEXT,
    target          DECIMAL(6,3) NOT NULL,              -- 99.5 (percent) or 500 (ms)
    unit            VARCHAR(20) NOT NULL,                -- percent, milliseconds, seconds
    sli_query       TEXT NOT NULL,                       -- PromQL query
    during_chaos_target DECIMAL(6,3),                    -- Relaxed target during experiments
    error_budget_days INT DEFAULT 30,                    -- Rolling window for error budget
    created_at      TIMESTAMP DEFAULT NOW()
);

-- SLO error budget consumption
CREATE TABLE error_budget_events (
    id              SERIAL PRIMARY KEY,
    slo_id          INT REFERENCES slo_definitions(id),
    execution_id    BIGINT REFERENCES experiment_executions(id),
    budget_consumed DECIMAL(6,4),                        -- How much error budget this experiment used
    budget_remaining DECIMAL(6,4),                       -- Remaining error budget percentage
    sli_value       DECIMAL(10,4),                       -- Actual SLI during experiment
    recorded_at     TIMESTAMP DEFAULT NOW()
);
```

---

## 7. Caching & Messaging — Redis vs Kafka vs Zookeeper vs RabbitMQ

> **Rev 2: the verdict is unchanged (Redis/Valkey Streams) but two things are amended.** Zookeeper and RabbitMQ are answered in §C.8 (the brief asked and Rev 1 skipped them), and the experiment lock in Purpose 4 is now **fenced by execution id** — an unfenced lock lets a stalled runner resume and act while a successor holds it (§C.2).

**Purpose 1 — Dashboard Cache**
```
Key: "dashboard:score:current"        → Latest resilience score (TTL: 5 min)
Key: "dashboard:score:trend:30d"      → 30-day trend data (TTL: 1h)
Key: "dashboard:experiments:latest"   → Latest execution per type (TTL: 5 min)
```

**Purpose 2 — Experiment Job Queue (Redis Streams)**
```
Stream: "chaos-experiments"
Producer: Scheduler (daily 3 AM) or CI trigger
  XADD chaos-experiments * type pod_kill trigger daily namespace target-app
Consumer: Experiment runner
  XREADGROUP GROUP runners worker-1 COUNT 1 BLOCK 5000

Why queue? Experiments must run SEQUENTIALLY — two experiments simultaneously
would invalidate each other's results. Redis Stream with single consumer ensures
serial execution.
```

**Purpose 3 — Prometheus Query Cache**
```
Key: "prom:baseline:{hash}"           → Cached baseline metrics (TTL: 60s)
Key: "prom:alert:{hash}"              → Cached alert query results (TTL: 30s)
```

**Purpose 4 — Experiment Lock (prevent concurrent runs)**
```
Key: "experiment:lock"                → SET NX, TTL 600s (10 min max experiment time)
Before running: acquire lock → if locked, skip (another experiment running)
After completion: release lock
```

**Why NOT Kafka?** Under 50 experiment events per day. Redis Streams with single-consumer serial processing is exactly what we need.

---

## 8. Design Patterns Used

### 8.1 Template Method (Experiment Lifecycle)

Every experiment follows the same lifecycle: baseline → inject → observe → validate → score → report. The `ExperimentRunner` defines this template. Individual validators implement specific check logic.

### 8.2 Strategy Pattern (Validation Checks)

```python
class ValidationStrategy(ABC):
    @abstractmethod
    async def validate(self, experiment, context) -> ValidationResult: ...

class SLOValidation(ValidationStrategy): ...
class AlertValidation(ValidationStrategy): ...
class ResiliencePatternValidation(ValidationStrategy): ...
class RecoveryValidation(ValidationStrategy): ...

# Each experiment can have a different set of validation strategies
```

### 8.3 Observer Pattern (Experiment Events)

```python
event_bus.on('experiment:started', log_to_database)
event_bus.on('experiment:fault_injected', start_observation_polling)
event_bus.on('experiment:completed', compute_score)
event_bus.on('experiment:completed', post_to_slack)
event_bus.on('experiment:completed', update_dashboard_cache)
event_bus.on('experiment:completed', update_error_budget)
event_bus.on('experiment:failed', alert_on_call)  # If critical experiment fails
```

### 8.4 Builder Pattern (Slack Message + LitmusChaos CRD)

```python
class SlackExperimentReport:
    def with_header(self, experiment_name, status): ...
    def with_score_gauge(self, score): ...
    def with_check_results(self, checks): ...
    def with_recovery_timeline(self, timeline): ...
    def with_grafana_link(self, time_range): ...
    def with_feedback_buttons(self): ...
    def build(self) -> dict: ...
```

### 8.5 Circuit Breaker (Prometheus + K8s API)

```python
@circuit(failure_threshold=3, recovery_timeout=30)
async def query_prometheus(query: str):
    return await prometheus_client.query(query)

# If Prometheus is down during an experiment, the validation
# fails fast instead of hanging on timeouts
```

### 8.6 Chain of Responsibility (Validation Pipeline)

```python
# Validations run in chain — each adds to the result context
chain = SLOValidator()
chain.set_next(AlertValidator()) \
     .set_next(ResiliencePatternValidator()) \
     .set_next(RecoveryValidator())

result = await chain.handle(experiment_context)
```

---

## 9. Docker & Kubernetes Deployment

### 9.1 Docker Compose (Local Dev)

> **Rev 2 fixes three things here (§C.2):** `grafana/grafana:latest` is pinned to `13.0.0`, the obsolete top-level `version:` key is removed (Compose v2 ignores it and warns), and a **k6 service is added** — a local stack with no load generator reproduces defect D1 on the developer's laptop. Postgres and Valkey tags are pinned exactly.

```yaml
services:
  order-api:
    build: ./target-app/order-api
    ports: ["8080:8080"]
    environment:
      - PAYMENT_SERVICE_URL=http://payment-service:8081
      - INVENTORY_SERVICE_URL=http://inventory-service:8082

  payment-service:
    build: ./target-app/payment-service
    ports: ["8081:8081"]

  inventory-service:
    build: ./target-app/inventory-service
    ports: ["8082:8082"]
    environment:
      - REDIS_URL=redis://redis:6379

  dashboard:
    build: ./dashboard
    ports: ["3000:3000"]
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/chaosproof
      - REDIS_URL=redis://redis:6379

  postgres:
    image: postgres:18.6-alpine
    ports: ["5432:5432"]
    environment: { POSTGRES_DB: chaosproof, POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres }
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]

  redis:
    image: valkey/valkey:9-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  grafana:
    image: grafana/grafana:13.0.0
    ports: ["3001:3000"]
    volumes: [./monitoring/grafana:/etc/grafana/provisioning]

volumes:
  postgres_data:
```

### 9.2 RBAC for Chaos Experiments

> **Rev 2 narrows this and justifies every verb in §26.2.** Two additions are required: `k6.io/testruns` (the load plane) and `deployments/scale` **patch** — the only mutating verb, held so cleanup can restore replica counts it changed (§15.6). `horizontalpodautoscalers` get/list is added so the CPU-spike experiment can detect an HPA owning the workload (§C.2).

```yaml
# charts/chaosproof/templates/rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: chaosproof-runner
  namespace: chaosproof
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: chaosproof-chaos-role
rules:
  # LitmusChaos CRD management
  - apiGroups: ["litmuschaos.io"]
    resources: ["chaosengines", "chaosexperiments", "chaosresults"]
    verbs: ["get", "list", "create", "delete", "patch"]
  # Pod observation
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events", "nodes"]
    verbs: ["get", "list", "watch"]
  # Deployment observation
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "watch"]
```

---

## 10. CI/CD — Chaos in the Pipeline (The Innovation)

> **Rev 2 adds four gates and one ordering fix — see §21.5.** New jobs: `replay-eval` (hermetic, replays committed evidence bundles through the scorer), `policy-eval` (every safety rule has a must-allow and must-deny case), `alert_rule_lint` (fails the build on alert rules whose irreducible latency exceeds their own SLO — §16.5), and `contract-gate` (surfaces weakened resilience contracts to their owners — §19.3). The ordering fix is the important one: **the load plane must start before the experiment runner**, or the CI gate reproduces defect D1 inside the pipeline, where a green badge hides it. Also: the gate only blocks on experiments that have earned gating status through flakiness characterisation (§21.3).

```yaml
# .github/workflows/chaos-gate.yml
name: Chaos Gate — PR Must Survive Chaos

on:
  pull_request:
    paths: ['target-app/**', 'charts/**']

jobs:
  chaos-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Create kind cluster
        uses: helm/kind-action@v1

      - name: Install dependencies
        run: |
          helm install prometheus prometheus-community/kube-prometheus-stack --wait
          helm install litmus litmuschaos/litmus --namespace litmus --create-namespace --wait

      - name: Deploy target app
        run: |
          docker build -t order-api:test ./target-app/order-api
          docker build -t payment-service:test ./target-app/payment-service
          docker build -t inventory-service:test ./target-app/inventory-service
          kind load docker-image order-api:test payment-service:test inventory-service:test
          helm install target-app ./charts/target-app --set image.tag=test --wait

      - name: Wait for app to stabilize
        run: |
          kubectl wait --for=condition=ready pod -l app=order-api --timeout=120s
          sleep 30  # Let Prometheus scrape initial metrics

      - name: Run Chaos Experiment — Pod Kill
        run: |
          python -m chaos_framework.src.main \
            --experiment pod_kill \
            --trigger ci_pipeline \
            --pr ${{ github.event.pull_request.number }} \
            --ci-mode  # Fail fast, minimal recovery window

      - name: Run Chaos Experiment — Network Latency
        run: |
          python -m chaos_framework.src.main \
            --experiment network_latency \
            --trigger ci_pipeline \
            --pr ${{ github.event.pull_request.number }} \
            --ci-mode

      - name: Evaluate Results
        id: chaos_results
        run: |
          SCORE=$(python -m chaos_framework.src.main --ci-evaluate)
          echo "score=$SCORE" >> $GITHUB_OUTPUT
          if (( $(echo "$SCORE < 0.7" | bc -l) )); then
            echo "❌ Chaos gate FAILED: resilience score $SCORE < 0.7"
            exit 1
          fi
          echo "✅ Chaos gate PASSED: resilience score $SCORE"

      - name: Post PR Comment
        if: always()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('chaos-report.md', 'utf8');
            github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: report
            });
```

**The Demo Moment:**
```
1. Open PR that removes Resilience4j circuit breaker from order-api
2. Chaos gate runs → pod kill experiment → payment-service fails
3. Without circuit breaker → order-api returns 500 for ALL requests
4. Validation: SLO recovery FAILED, resilience pattern FAILED
5. PR status: ❌ BLOCKED — "Resilience score: 0.35 (required: 0.70)"
6. Developer adds circuit breaker back → push → chaos gate reruns → ✅ PASSED

INTERVIEW LINE: "A PR that breaks resilience can't reach production.
The chaos gate proved the circuit breaker was load-bearing — removing it
caused 100% failure under pod kill. That's shift-left chaos engineering."
```

---

## 11. Monitoring — The "Scaling Decision"

> **Rev 2 keeps this decision and adds two more.** Sequential-over-parallel is correct and unchanged. The second decision is **open over closed workload model** (§16.2) — arguably the stronger interview moment, because a closed model silently reduces offered load when the system degrades and hides the fault. The third is **bounded bisection with visible cost** (§20.3). Rev 2 also adds panels for hypothesis verdicts, SLI validity, the client-vs-server availability gap, flakiness, and the error-budget gate state (§16.6).

> **Scaling Decision: Sequential Experiment Execution over Parallel**
>
> **Problem:** Running 6 experiments takes ~15 minutes sequentially. Could we run them in parallel to finish in ~3 minutes?
>
> **Option A — Parallel execution:** Run all 6 experiments simultaneously. Pros: fast. Cons: experiments interfere with each other. A CPU spike concurrent with a network partition makes it impossible to attribute which fault caused which SLI violation. Validation becomes meaningless.
>
> **Option B — Sequential execution:** Run experiments one at a time with a recovery window between each. Pros: clean isolation, attributable results. Cons: 15 minutes total.
>
> **Decision:** Option B. Each experiment must run in isolation so we can definitively say "pod kill caused availability to drop to 94%, and recovery took 12 seconds." If pod kill and network latency run simultaneously, we can't distinguish their effects. The 15-minute total is acceptable for a 3 AM scheduled run. For CI, we run only 2 experiments (pod kill + network latency) taking ~5 minutes — fast enough for PR checks.
>
> **When I'd parallelize:** If we monitored 50+ microservices and needed to test each independently, I'd run experiments in parallel across DIFFERENT services (pod kill on service A while network latency on service B). Same-service experiments always run sequentially.

---

## 12. Interview Prep — Top Questions & Answers

> **Rev 2 adds fifteen more in §28**, including the four questions this version of the project would have failed: *how do you know your experiments measure anything*, *how can you measure a 30-second recovery SLO with 5-minute windows*, *did the scoring change during those four weeks*, and *why would your team still have the CI gate enabled in six months*. Note that **Q7's answer below describes safeguards Rev 1 did not implement** — the pre-flight health check and any in-experiment abort. §15 builds all of them; do not give Q7's answer until it is true.

**Q1: "Walk me through the architecture."**

> "A Spring Boot microservices application runs on Kubernetes with Resilience4j circuit breakers, retries, and bulkheads. Prometheus monitors application SLIs — availability, latency, and Resilience4j pattern states. LitmusChaos provides the chaos experiment engine as Kubernetes CRDs. My Python validation framework orchestrates experiments: capture baseline metrics, inject fault via LitmusChaos, observe SLI degradation during the fault, then validate four things after the fault ends: did the SLI recover within the SLO target? Did Prometheus alert fire within 60 seconds? Did the circuit breaker open? Is the system fully healthy now? Each check gets a score, and the composite score feeds into a resilience trend on the Next.js dashboard. The innovation is embedding this in CI — every PR runs pod-kill and network-latency experiments and must score above 0.70 to merge."

**Q2: "What's the difference between SLOs, SLIs, and error budgets?"**

> "SLI is the measurement — 'what percentage of requests succeed?' SLO is the target — 'we promise 99.5% success rate.' Error budget is the acceptable failure margin — 0.5% of requests can fail before we violate the SLO. In ChaosProof, each experiment consumes error budget. If availability drops to 95% during a pod kill experiment for 30 seconds, that's 5% unavailability × 30 seconds = budget consumed. I track cumulative budget consumption over a 30-day rolling window. If experiments consistently consume too much budget, it means the system isn't resilient enough — the score reflects this. The SRE philosophy is: if error budget is exhausted, freeze deployments and focus on reliability."

**Q3: "Why LitmusChaos over Chaos Mesh?"**

> "Both are excellent. I chose LitmusChaos because it's a CNCF incubating project with broader community adoption, the ChaosEngine CRD model makes experiments declarative and GitOps-friendly, it has 50+ pre-built experiments, and the built-in Litmus probes let me define pass/fail criteria directly in the experiment YAML. Chaos Mesh has better network chaos capabilities in some edge cases, but for the six experiment types I need — pod kill, network latency, network loss, disk fill, CPU hog, and container kill — LitmusChaos covers all of them. The CNCF backing also matters for interview value."

**Q4: "How does the circuit breaker validation work?"**

> "Resilience4j exposes circuit breaker state as a Prometheus metric: `resilience4j_circuitbreaker_state`. Value 0 means CLOSED (normal), 1 means OPEN (failing fast), 2 means HALF_OPEN (testing recovery). My validator queries this metric over the experiment time range. For a network partition experiment, I expect the circuit breaker on order-api's payment-service call to transition CLOSED → OPEN within 10 seconds of the fault. After the fault ends, I expect it to go OPEN → HALF_OPEN → CLOSED within the recovery window. If the circuit breaker stays CLOSED during a downstream failure, it means the pattern isn't working — traffic is hitting the dead service instead of failing fast. That's a FAIL."

**Q5: "Explain chaos in CI."**

> "When a developer opens a PR that modifies the application code or Helm charts, GitHub Actions deploys the PR version to a kind cluster, installs LitmusChaos, and runs two experiments: pod kill and 500ms network latency. If the application survives — recovers within SLO, alerts fire, circuit breaker activates — the PR passes. If not, it's blocked. The powerful demo: I remove the `@CircuitBreaker` annotation from order-api's payment call, push the PR, and the chaos gate fails because without the circuit breaker, a pod kill on payment-service causes order-api to hang instead of failing fast. The developer can't merge until they restore the resilience pattern. This is shift-left chaos engineering."

**Q6: "What's the '45% to 92%' story?"**

> "On day 1, I deployed the target app with minimal resilience: no circuit breakers, no retries, only 1 replica per service. The daily chaos run scored 45% — pod kills caused extended outages, alerts didn't fire (missing alert rules), and no resilience patterns existed to activate. Over 4 weeks, I systematically improved: Week 1 — added Resilience4j circuit breakers and retries (score jumped to 62%). Week 2 — configured proper Prometheus alert rules and adjusted thresholds (72%). Week 3 — increased replicas to 3 per service and tuned readiness probes (85%). Week 4 — added bulkhead isolation and cache fallbacks (92%). The trend chart tells this story visually. Each improvement is a commit in git — traceable, reviewable, and directly correlated to the score increase."

**Q7: "How do you prevent experiments from impacting production?"**

> "Four safeguards. First, namespace isolation — chaos experiments target the `target-app` namespace only, never other namespaces. The LitmusChaos ServiceAccount has RBAC limited to that namespace. Second, experiment limits — pod kill affects at most 50% of replicas (configurable), not all. Third, circuit breaker on chaos itself — if the baseline SLI is already degraded (below SLO before experiment), the experiment is skipped with a 'skipped — system already unhealthy' status. Fourth, scheduled timing — daily experiments run at 3 AM when traffic is lowest. In CI, experiments run on isolated kind clusters that don't affect production."

**Q8: "What RBAC does the chaos framework need?"**

> "A ClusterRole with specific permissions. It can GET/LIST/CREATE/DELETE LitmusChaos CRDs (ChaosEngine, ChaosExperiment, ChaosResult) — this is how we trigger and monitor experiments. It can GET/LIST/WATCH pods, deployments, and events — for observation during experiments. It CANNOT delete deployments, modify RBAC, or access secrets. The ServiceAccount is bound only to the `target-app` and `chaosproof` namespaces. This follows principle of least privilege — the chaos framework can inject faults and observe results, but can't do anything beyond what the experiments require."

**Q9: "Describe the demo."**

> "I open the dashboard — resilience score is 92%, all green. I trigger a manual pod-kill experiment. The dashboard shows a live timeline: fault injected → availability drops to 96% → circuit breaker opens (15s) → alert fires (22s) → fault ends → pods recreate → availability returns to 99.9% (total recovery: 12s). Score: 1.0. Then I trigger a network partition. Payment-service becomes unreachable → circuit breaker on order-api opens → fallback returns 'payment queued' → alert fires → partition ends → circuit breaker recovers → score: 0.92. Slack gets both results with Grafana deep links. Then the dramatic demo: I open a PR removing the circuit breaker → chaos gate fails → 'This PR reduces resilience. Score: 0.35.' That's the moment interviewers remember."

---

## 13. Deployment Checklist

> **Rev 2 adds to this checklist:** load plane running and validity gate proven (trigger an experiment with load stopped and confirm `INVALID`) · pre-flight gate returning `SKIPPED` on a degraded baseline · an abort demonstrated mid-fault with cleanup verified · `resilience4j_*` series present at startup or readiness fails · alert-rule lint passing · one experiment auto-quarantined for flakiness · an epoch boundary visible on the trend chart · `chaosctl replay <sha>` byte-identical offline · evidence bundles signed in CI.

- [ ] K8s cluster with Prometheus + LitmusChaos installed
- [ ] Target app with Resilience4j patterns, metrics flowing to Prometheus
- [ ] All 6 chaos experiments run and complete successfully
- [ ] Validation framework: SLO, alert, resilience pattern, and recovery checks pass
- [ ] Resilience score calculated correctly
- [ ] Dashboard: score gauge, trend chart, experiment timeline, check results
- [ ] Slack notifications with rich context and Grafana links
- [ ] CI chaos gate: PR blocked when resilience is broken
- [ ] "45% to 92%" improvement story documented with git history
- [ ] Daily CronJob scheduling experiments at 3 AM
- [ ] Docker Compose for local development
- [ ] Helm chart for deployment
- [ ] README: architecture diagram, screenshots, demo video
- [ ] Demo video: live experiment + CI gate blocking a bad PR

### Cost Estimate

| Service | Provider | Cost |
|---|---|---|
| GKE Autopilot (small cluster) | Google Cloud | ~$70/mo ($300 free credit) |
| OR kind on EC2 t3.medium | AWS | ~$30/mo |
| OR local minikube | Local | $0 |
| Slack App | Slack | Free |
| **Total** | | **$0-70/month** |

---

## C. Rev 2 Corrections — The Six Defects

Rev 1 was reviewed as if a staff SRE were asked to sign off on the resilience score appearing in a board deck. Six findings were real defects, not polish. Fixing them in public — in the README and in the interview — is worth more than never having had them.

### C.1 Summary

| # | Where | Defect | What happens in practice | Fix |
|---|---|---|---|---|
| **D1** | §2 stack, §4 flow, §5.2 | **No load generator anywhere in the project.** Every SLI is a ratio over `http_server_requests_seconds_count` | With no traffic, availability is **0/0**. A pod-kill experiment on an idle cluster reports "recovered, zero 5xx" — because there were zero requests. The headline score is measuring nothing | §16.1–§16.2 — k6 load plane, open workload model, **SLI-validity gate** with an `INVALID` verdict |
| **D2** | §4 flow, §5.2 `_capture_baseline` | **Recovery measured with 5-minute rate windows against a 30-second SLO.** `rate(...[5m])` and `histogram_quantile` over `[5m]` | The metric physically cannot move within 30s. Every recovery-time number is dominated by window lag, not system behaviour. "Recovered in 12.4s" is unattainable from a 5m window | §16.3 — 5s scrape, ≥30s windows, client-side k6 timing as the primary recovery clock |
| **D3** | §4 score calc | **Non-applicable checks are silently scored 0.5.** The worked example proves it: disk-fill with `pattern=N/A` yields 0.625, which is only reachable as `0.35 + 0 + (0.5 × 0.25) + 0.15` | Every experiment with no applicable resilience pattern gets a free 12.5 points. The score is inflated and non-comparable across experiment types | §C.4 — `applicable` flag, weights **renormalised** over applicable checks |
| **D4** | §4, §11, §12 Q6 | **Scores compared across changing weights, SLOs, and experiment sets.** The 45%→92% narrative spans four weeks in which the experiment set and alert rules both changed | The trend chart is not a measurement of the system; it partly measures edits to the scorer. An interviewer who asks "did the scoring change during those four weeks?" ends the story | §21.1 — **scoring epochs**, retro-scoring from stored raw observations |
| **D5** | §12 Q7 vs §5.2 | **The safety story is claimed but not built.** Q7 promises "circuit breaker on chaos itself — skip if baseline SLI is already degraded"; no such check exists in `ExperimentRunner`, and there is no abort-on-harm once a fault is injected | The framework injects faults into an already-sick cluster and cannot stop a fault that is doing more damage than predicted. This is the one defect that could cause real harm | §15 — pre-flight blast radius, Litmus probes as in-experiment aborts, independent watchdog |
| **D6** | §3 wk 6, §6.2 | **Error budget is recorded but never enforced.** `error_budget_events` accumulates; nothing reads it | The SRE-correct behaviour — stop injecting failure when you have already spent your reliability allowance — is described in the interview answer for Q2 and implemented nowhere | §15.5 — error-budget gate, three states, documented policy |

### C.2 Six smaller corrections

| Where | Issue | Correction |
|---|---|---|
| §3 experiment 6 | "Container Kill — Docker-level" | Docker shim was removed in K8s 1.24; nodes run **containerd** (or CRI-O). Litmus `container-kill` talks to the CRI socket. Say "container runtime level," and set `CONTAINER_RUNTIME: containerd` with the right `SOCKET_PATH` |
| §3 experiment 4 | Disk fill "fills ephemeral storage to 90%" → expects `NodeDiskPressure` | These are **two different failure modes**. Litmus `disk-fill` consumes the *container's* ephemeral storage and requires `ephemeral-storage` limits set on the pod; exceeding them triggers **pod eviction**, not necessarily node-level `DiskPressure`. Pick one: assert eviction-and-reschedule (recommended, deterministic), or fill the node's filesystem and assert `NodeDiskPressure`. Rev 2 asserts eviction |
| §3 experiment 5 | CPU spike expects "HPA scales replicas → recovery" | **The scaling paradox:** `pod-cpu-hog` burns CPU *inside the target container*, so HPA sees high utilisation and adds replicas — each of which is healthy, while the hogged pod stays hogged. Scaling does not relieve the injected load, and if CPU limits are set the hog is throttled and utilisation may barely move. Assert what actually should happen: **throttle ratio rises, latency degrades within budget, HPA reacts, and the *aggregate* SLI holds** — not "the spike goes away" |
| §7 purpose 4 | Redis lock `SET NX` with 600s TTL, unfenced | A runner that stalls past the TTL can resume and act while a successor holds the lock. **Fence with the execution id** and verify ownership before every mutating call |
| §9.1 | `grafana/grafana:latest`, obsolete `version: '3.8'`, no load generator | Pin Grafana to `13.0.0`, drop the `version` key (Compose v2 ignores it and warns), add the k6 service |
| §5.2 `_observe_during_fault` | `await asyncio.sleep(5)` after sequential awaits | The sample period is 5s **plus** query latency, so samples drift and the timeline is skewed. Use a fixed-deadline scheduler (`next_tick += 5`) and record the *actual* sample timestamps, which Rev 2 needs anyway for §16.3 |

### C.3 D1 — the load plane, stated plainly

This is the correction that changes the project's credibility most, so it gets said in one paragraph you can reuse verbatim:

> *"My first version had a measurement bug that invalidated the headline number: there was no load generator. Every SLI I computed was a ratio of successful requests to total requests, and with no traffic during the experiment that ratio is zero over zero. A pod kill against an idle service looks like a perfect recovery, because nothing was in flight to fail. So Rev 2 added a load plane — k6 running in-cluster via the k6 Operator, holding a constant arrival rate through the entire experiment, plus a validity gate that marks a verdict `INVALID` rather than `PASS` when request rate is below the floor I need to measure anything. No traffic means no evidence, and no evidence must never score as success."*

Full design in §16. The important structural consequence: **`INVALID` is a third verdict**, distinct from pass and fail, and it never contributes to the resilience score.

### C.4 D3 — the scoring fix, corrected

```python
# chaos-framework/src/scoring/score_calculator.py
"""
Rev 2. Two changes from Rev 1:
  1. A check declares whether it is APPLICABLE. Non-applicable checks are excluded
     and the remaining weights are renormalised — never scored as partial credit.
  2. An INVALID measurement (§16.1) poisons the whole experiment: no score at all.
"""
WEIGHTS = {                       # part of the scoring epoch (§21.1)
    "slo_recovery":         0.35,
    "alert_validation":     0.25,
    "resilience_pattern":   0.25,
    "recovery_completeness":0.15,
}

def calculate(checks: list[Check]) -> ScoreResult:
    if any(c.outcome == "invalid" for c in checks):
        return ScoreResult(score=None, status="invalid",
                           reason="SLI validity floor not met — experiment not scoreable")

    applicable = [c for c in checks if c.applicable]
    if not applicable:
        return ScoreResult(score=None, status="invalid", reason="no applicable checks")

    total_weight = sum(WEIGHTS[c.check_type] for c in applicable)
    score = sum(WEIGHTS[c.check_type] * c.score for c in applicable) / total_weight
    return ScoreResult(score=score, status=classify(score),
                       weights_denominator=total_weight,          # persisted, for audit
                       excluded=[c.check_type for c in checks if not c.applicable])
```

Re-scoring Rev 1's own worked example makes the size of the error visible:

| Experiment | Rev 1 score | Rev 2 score | Why it moved |
|---|---|---|---|
| Disk Fill (`pattern` not applicable, alert missed) | 0.625 | **0.6667** | `(0.35×1.0 + 0.25×0.0 + 0.15×1.0) / 0.75` — no free half-credit |
| Network Partition | 0.750 | 0.750 | All four checks applicable; unchanged |
| Daily aggregate (six experiments) | 87.5% | **88.2%** | The half-credit was *deflating* this experiment |

> **Arithmetic corrected 26 Aug 2026**, verified by `evals/scoring_worked_example.py`, which derives every figure from the weights rather than copying it. Three slips in the original: the Rev 2 disk-fill value was published as **0.588**, which is `0.50 / 0.85` — the 0.15 completeness weight excluded instead of the 0.25 pattern weight; the correct renormalisation is `0.50 / 0.75` = **0.6667**. Network Partition was published as 0.725 where the weights give 0.750, moving the Rev 1 daily from 87.1% to 87.5%. And the **direction flips**: disk-fill's applicable checks average 0.667, *above* the arbitrary 0.5, so the partial credit was deflating that experiment rather than inflating it.

The size of the move is not the point, and neither is its sign. The point is that **0.5 is unrelated to any evidence**: it applies to exactly the experiments with no pattern to validate, and it pushes each one in whichever direction its remaining checks happen to sit — up when they average below 0.5, down when they average above, as disk-fill's 0.667 does here. That is worse than a consistent bias, because no offset corrects it, and it makes scores non-comparable across experiment types. That non-comparability is what an interviewer will catch.

### C.5 D5 — the missing safety check, made real

Rev 1's Q7 answer describes four safeguards. Two exist (namespace scoping, RBAC). Two do not: the pre-flight health check and any in-experiment abort. Rev 2 implements all four plus two more, in §15. The minimum viable version, which belongs in `ExperimentRunner` before anything else runs:

```python
# Pre-flight. Runs BEFORE the Litmus CRD is applied.
async def preflight(self, experiment) -> PreflightVerdict:
    # 1. Is the system already unhealthy? Injecting into a sick cluster is not an experiment.
    if not await self.steady_state.holds(experiment.hypothesis, window_s=120):
        return PreflightVerdict.skip("steady state not established before injection")

    # 2. Is there enough traffic to measure anything? (§16.1)
    if await self.load.current_rps() < experiment.min_rps_floor:
        return PreflightVerdict.skip("below SLI validity floor — start the load plane first")

    # 3. Would the blast radius exceed budget? (§15.2)
    radius = self.blast.compute(experiment)
    if radius.error_budget_burn_pct > experiment.max_budget_burn_pct:
        return PreflightVerdict.deny(f"would burn {radius.error_budget_burn_pct:.1f}% of budget")

    # 4. Is the error budget already spent, or is chaos frozen? (§15.5)
    gate = await self.budget.gate(experiment.target_namespace)
    if gate.state != "open":
        return PreflightVerdict.deny(f"budget gate {gate.state}: {gate.reason}")

    return PreflightVerdict.proceed(radius)
```

**A skip is a recorded outcome, not a silent no-op.** `skipped` and `denied` are first-class statuses with their own Slack messages and dashboard treatment, because "the framework correctly refused to run" is a data point that proves the guardrails are load-bearing.

### C.6 The Spring Boot 4 resilience decision (new, and worth the paragraph)

Spring Boot 4 ships native resilience annotations — `@Retryable`, `@ConcurrencyLimit`, `@EnableResilientMethods` — which removes the need for Resilience4j in many applications. For ChaosProof it is the wrong choice, for a reason specific to this project:

| | Spring Boot 4 native | **Resilience4j 3.x** |
|---|---|---|
| Retry | ✅ `@Retryable` | ✅ |
| Concurrency limiting | ✅ `@ConcurrencyLimit` | ✅ bulkhead |
| **Circuit breaker** | ❌ **not provided** | ✅ with observable state machine |
| Rate limiter | ❌ | ✅ |
| **Prometheus state metrics** | Limited; no `*_state` gauge to read | ✅ `resilience4j_circuitbreaker_state`, `_calls_total`, `resilience4j_retry_calls_total`, `resilience4j_bulkhead_available_concurrent_calls` |
| Verdict for this project | Rejected | **Chosen** |

The circuit breaker is the single most important pattern ChaosProof validates, and native Spring does not have one. More fundamentally: **this project can only validate patterns that publish their state.** That is a real architectural constraint and a good interview answer — it shows you chose a dependency for its observability rather than its novelty. Use Boot 4's `@Retryable` where you want retry *without* verification, and Resilience4j everywhere the validator needs to see inside.

Two build traps from §B, repeated because they cost hours: the Spring Boot 4 artifact is **`resilience4j-spring-boot4`** (support landed in the 2.4.0 line, and the artifact was missing from the BOM as of March 2026 — verify or pin explicitly), and **`resilience4j-micrometer` is not a transitive dependency**. Without it, every `resilience4j_*` query in this plan returns empty, and the pattern validator will report `applicable=false` for everything while looking like it works.

### C.7 Amendments to §6 — the completed matrix and the new tables

The brief asked for six database candidates; Rev 1 compared four. Completing it:

| Criteria | **PostgreSQL 18.6** | TimescaleDB | MongoDB | Cassandra | CosmosDB | Generic NoSQL (DynamoDB etc.) |
|---|---|---|---|---|---|---|
| **Event rate** | ~120 check rows/day + SLI samples. Trivial | Built for millions/s | Fine | Absurd | Fine | Fine |
| **Core query** | "all checks for execution #47 with SLO + hypothesis + contract" = 4–5 JOINs | Same (is Postgres) | `$lookup` chains | Not for analytical JOINs | SQL API workable | No JOINs |
| **Trend query** | `GROUP BY` epoch, date → 30 rows | Continuous aggregates (overkill) | Aggregation pipeline | Poor | Workable | Client-side |
| **Transactional integrity** | ACID: execution + all checks + score in one transaction | Same | Single-doc only | Eventual | Configurable, costly | Limited |
| **JSONB for raw observations** | ✅ native, indexable | ✅ | ✅ native | ❌ | ✅ | ✅ |
| **Cost at this scale** | Free, one container | Free + extension ops | Free | High ops | **Consumption billing on a demo = surprise bill** | Cloud lock-in |
| **Verdict** | ✅ **Chosen** | ❌ | ❌ | ❌ | ❌ | ❌ |

> **Where TimescaleDB *nearly* wins, and the honest answer.** Rev 2 adds `sli_samples` — 5-second-resolution SLI samples during experiments (§16.3). That *is* time-series data: roughly 1,200 rows per experiment, ~7,000/day. Still three orders of magnitude below where hypertables earn their complexity, and the rows are written in one burst and read once, so retention is a `DELETE` on a partitioned table rather than a compression policy. **Plain PostgreSQL with monthly partitions on `sli_samples`, and I can say exactly what would change my mind: continuous 5-second sampling of a hundred services, not six experiments a day.** Naming your own threshold for switching is what separates a decision from a preference — and note the deliberate contrast with the sibling project (KubeThrifty) where the same reasoning selected TimescaleDB *for* high-frequency metrics.

```sql
-- migrations/002_rev2.sql   (PostgreSQL 18)

-- 1. Steady-state hypotheses (§14). Versioned, because changing one changes the epoch.
CREATE TABLE hypotheses (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    experiment_type_id  INT NOT NULL REFERENCES experiment_types(id),
    version             INT NOT NULL,
    description         TEXT NOT NULL,
    invariants          JSONB NOT NULL,      -- [{name, promql, comparator, threshold, tolerance_s}]
    min_rps_floor       NUMERIC(8,2) NOT NULL,
    abort_conditions    JSONB NOT NULL,      -- [{name, promql, comparator, threshold}]
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_type_id, version)
);

-- 2. Per-invariant outcome. The verdict is per-invariant, not one blob.
CREATE TABLE hypothesis_results (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    execution_id    BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    hypothesis_id   UUID NOT NULL REFERENCES hypotheses(id),
    invariant_name  TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('held','falsified','invalid')),
    worst_value     NUMERIC(14,4),
    threshold       NUMERIC(14,4),
    breached_for_s  NUMERIC(8,2),
    evidence        JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX ON hypothesis_results (execution_id);

-- 3. The load run that made the experiment measurable (§16). No load row, no score.
CREATE TABLE load_runs (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    execution_id        BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    tool                TEXT NOT NULL DEFAULT 'k6',
    tool_version        TEXT NOT NULL,
    workload_model      TEXT NOT NULL CHECK (workload_model IN ('open','closed')),
    target_rps          NUMERIC(8,2) NOT NULL,
    achieved_rps        NUMERIC(8,2),                  -- dropped_iterations shows up here
    dropped_iterations  BIGINT DEFAULT 0,
    client_error_rate   NUMERIC(6,4),                  -- what the CLIENT saw (§16.2)
    client_p99_ms       NUMERIC(10,2),
    script_sha256       CHAR(64) NOT NULL,             -- the load profile is part of the epoch
    raw_summary         JSONB NOT NULL DEFAULT '{}'
);

-- 4. High-resolution SLI samples. Partitioned monthly; this is the retro-scoring substrate.
CREATE TABLE sli_samples (
    execution_id    BIGINT NOT NULL,
    sampled_at      TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('prometheus','k6')),
    metric          TEXT NOT NULL,
    value           NUMERIC(14,4),
    PRIMARY KEY (execution_id, sampled_at, source, metric)
) PARTITION BY RANGE (sampled_at);
CREATE TABLE sli_samples_2026_08 PARTITION OF sli_samples
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- 5. Scoring epochs (§21.1). A trend line may only be drawn within one epoch.
CREATE TABLE scoring_epochs (
    id              SERIAL PRIMARY KEY,
    epoch_sha256    CHAR(64) UNIQUE NOT NULL,   -- hash(weights, experiment set, SLOs, scorer version)
    weights         JSONB NOT NULL,
    experiment_set  TEXT[] NOT NULL,
    slo_version     INT NOT NULL,
    scorer_version  TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    change_reason   TEXT NOT NULL
);
ALTER TABLE experiment_executions
    ADD COLUMN IF NOT EXISTS scoring_epoch_id INT REFERENCES scoring_epochs(id),
    ADD COLUMN IF NOT EXISTS hypothesis_id UUID REFERENCES hypotheses(id),
    ADD COLUMN IF NOT EXISTS verdict TEXT CHECK (verdict IN
        ('held','falsified','invalid','skipped','denied','aborted','error')),
    ADD COLUMN IF NOT EXISTS abort_reason TEXT,
    ADD COLUMN IF NOT EXISTS blast_radius JSONB,
    ADD COLUMN IF NOT EXISTS bundle_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS git_sha CHAR(40),          -- for bisection (§20)
    ADD COLUMN IF NOT EXISTS weights_denominator NUMERIC(4,3);   -- audit trail for §C.4

-- 6. Service resilience contracts (§19).
CREATE TABLE resilience_contracts (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    service         TEXT NOT NULL,
    version         TEXT NOT NULL,
    provides        JSONB NOT NULL,
    tolerates       JSONB NOT NULL,
    does_not_inflict JSONB NOT NULL DEFAULT '[]',
    contract_sha256 CHAR(64) NOT NULL,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (service, version)
);
CREATE TABLE contract_validations (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    contract_id     UUID NOT NULL REFERENCES resilience_contracts(id),
    execution_id    BIGINT NOT NULL REFERENCES experiment_executions(id),
    clause          TEXT NOT NULL,             -- "tolerates.payment-svc.max_latency_ms=800"
    outcome         TEXT NOT NULL CHECK (outcome IN ('honoured','violated','untested')),
    observed        JSONB NOT NULL DEFAULT '{}'
);

-- 7. Counterfactual pairs (§17). n repetitions per arm; store every run, report the distribution.
CREATE TABLE counterfactual_runs (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    pair_id         UUID NOT NULL,             -- groups the with/without arms
    execution_id    BIGINT NOT NULL REFERENCES experiment_executions(id),
    arm             TEXT NOT NULL CHECK (arm IN ('with_pattern','without_pattern')),
    pattern_name    TEXT NOT NULL,
    repetition      INT NOT NULL,
    failed_requests BIGINT,
    p99_ms          NUMERIC(10,2),
    recovery_s      NUMERIC(8,2)
);

-- 8. Flakiness tracking (§21.3). A flaky experiment must not gate a merge.
CREATE TABLE experiment_flakiness (
    experiment_type_id  INT PRIMARY KEY REFERENCES experiment_types(id),
    window_runs         INT NOT NULL,
    score_stddev        NUMERIC(6,4),
    verdict_flip_rate   NUMERIC(5,4),          -- flips on unchanged git_sha
    gating              BOOLEAN NOT NULL DEFAULT TRUE,
    quarantined_at      TIMESTAMPTZ,
    quarantine_reason   TEXT
);
```

Note the deliberate choices: **`uuidv7()`** for the new tables (timestamp-ordered keys, good index locality, and the evidence bundle reads chronologically); **partitioning `sli_samples` by month** before it needs it; and **`weights_denominator` persisted on every execution**, so a stored score can be re-derived and audited rather than merely trusted.

### C.8 Amendment to §7 — the Zookeeper and RabbitMQ answers

The brief asked about Redis vs Kafka vs **Zookeeper** vs **RabbitMQ**; Rev 1 rejected only Kafka. Completing it, and the Zookeeper answer is a category correction, which is the best kind:

| Option | What it actually is | Verdict for ChaosProof |
|---|---|---|
| **Redis / Valkey Streams** | In-memory store with a log type, consumer groups, `SET NX` locks, TTLs | ✅ **Chosen.** One dependency covers the serial experiment queue, the dedup set, the fenced experiment lock, and the dashboard cache. Under 50 experiment events/day |
| Kafka | Distributed partitioned commit log | ❌ Built for millions of events/sec. A 3-broker cluster for ~50 messages/day is a punchline. (Worth knowing: **Kafka no longer needs Zookeeper** — KRaft replaced it) |
| **Zookeeper** | **Not a message queue** — a coordination service (consensus, leader election, distributed config) | ❌ **Category error to compare against a queue.** And the thing I *would* want it for — making sure only one experiment runs at a time across replicas — is better served by the **Kubernetes `Lease` API**, which is already in the cluster |
| RabbitMQ | AMQP broker, rich routing, per-message ack, DLQ | ❌ Technically fine, and its per-message ack is arguably nicer than `XACK`. But it adds an Erlang service to do one job Streams already does, and I would still need Redis for the lock, the dedup set, and the cache. One dependency beats two |

> Interview soundbite: *"Zookeeper isn't a queue — it's a coordination service, so comparing it to Redis or RabbitMQ is a category error. The interesting part is that I do have a coordination problem: only one experiment may run at a time, cluster-wide. I solve it with a fenced Redis lock plus a Kubernetes Lease for leader election, not by adding a Zookeeper ensemble. And Kafka itself dropped Zookeeper for KRaft, which tells you where that dependency went."*

### C.9 Amendments to §8 — five patterns Rev 2 adds

| Pattern | Rev 2 use | Why it earns its place |
|---|---|---|
| **Specification / hypothesis object** | The experiment declares invariants and abort conditions as data, evaluated by the framework (§14) | Makes the pass criterion reviewable in git instead of buried in validator code |
| **Saga / compensating transaction** | Cleanup is registered before injection: revert feature flags, delete the ChaosEngine, resume the load profile, restore replica counts (§15.6) | An aborted experiment must not leave the cluster mutated — this is the pattern that makes abort safe |
| **Token bucket** | Experiment budget per namespace per day, plus the error-budget gate (§15.5) | Rate-limits *changes to a live system*, not HTTP calls — the idea most chaos tooling misses |
| **Circuit breaker as policy** | Global chaos breaker trips on repeated aborts, repeated `INVALID` verdicts, or a sick cluster (§15.5) | Distinguishes transport resilience from *operational* resilience |
| **Content-addressed evidence** | `sha256` of the canonical experiment bundle is the run's identity, signed in CI (§22.3) | Turns "trust my dashboard" into "replay my hash" |

---

## 14. The Steady-State Hypothesis Engine

### 14.1 The idea

Rev 1 asks "did the system recover?" That is a binary with no stated expectation, which means the answer is whatever the validator happens to check. *Principles of Chaos Engineering* (Rosenthal, Basiri et al.) starts somewhere else: with a **steady-state hypothesis** — a formal, falsifiable claim about how the system behaves when healthy. The experiment exists to try to falsify it.

The practical difference is that the pass criterion moves out of Python and into reviewable YAML, and the verdict gains vocabulary:

```
Rev 1:  passed | failed | partial
Rev 2:  HYPOTHESIS_HELD | HYPOTHESIS_FALSIFIED | INVALID | SKIPPED | DENIED | ABORTED
```

`INVALID` is the one that matters most (§16.1): it means *the experiment could not measure what it claimed to measure*, and it must never be reported as a pass.

> Interview soundbite: *"Every experiment in my framework starts with a hypothesis — a falsifiable statement with quantified invariants — and the framework reports whether it held or was falsified. That's not vocabulary for its own sake. It moves the pass criterion into version-controlled YAML that a reviewer can argue with, and it forces me to write down what I expect *before* I see the result, which is the only way an experiment can surprise you. Chaos Monkey kills instances; a hypothesis engine tells you what you believed and whether you were wrong."*

### 14.2 The hypothesis object

```yaml
# experiments/pod_kill_payment.yaml
experiment:
  name: pod_kill_payment_svc
  litmus_fault: pod-delete
  target: { namespace: target-app, app: payment-service }
  fault_duration_s: 60
  recovery_window_s: 120

  # --- what makes this experiment measurable at all (§16.1) ---
  load_profile: profiles/steady_120rps.js
  min_rps_floor: 90              # below this, verdict = INVALID, not PASS

  hypothesis:
    version: 3
    description: >
      When one replica of payment-service is killed under 120 rps of steady traffic,
      the system maintains >=99.0% client-observed availability and client P99 latency
      stays under 800ms, because surviving replicas absorb traffic via the Service
      endpoints and order-api's circuit breaker never needs to open.

    invariants:
      - name: client_availability_holds
        source: k6                      # CLIENT-side, not server-side (§16.2)
        expr: 1 - (http_req_failed / http_reqs)
        comparator: ">="
        threshold: 0.990
        tolerance_s: 10                 # may breach for <=10s during endpoint convergence

      - name: client_p99_holds
        source: k6
        expr: http_req_duration_p99_ms
        comparator: "<="
        threshold: 800
        tolerance_s: 15

      - name: no_server_5xx_storm
        source: prometheus
        expr: |
          sum(rate(http_server_requests_seconds_count{status=~"5..",namespace="target-app"}[30s]))
        comparator: "<="
        threshold: 1.0
        tolerance_s: 10

      - name: replicas_restored
        source: prometheus
        expr: |
          sum(kube_deployment_status_replicas_available{deployment="payment-service"})
        comparator: ">="
        threshold: 3
        recover_within_s: 30            # this one is a RECOVERY invariant, not a hold-throughout

    # --- abort conditions: if these trip, stop the fault immediately (§15.3) ---
    abort_conditions:
      - name: availability_collapse
        source: k6
        expr: 1 - (http_req_failed / http_reqs)
        comparator: "<"
        threshold: 0.80                 # 20% failure is beyond any acceptable blast radius
      - name: unrelated_namespace_impact
        source: prometheus
        expr: |
          sum(rate(http_server_requests_seconds_count{status=~"5..",namespace!="target-app"}[30s]))
        comparator: ">"
        threshold: 0.5                  # we are leaking outside the blast radius
```

Two distinctions the schema makes explicit, and that Rev 1 conflated:

- **Hold-throughout invariants** (`tolerance_s`) may be briefly breached, then must return. These describe *degradation budget*.
- **Recovery invariants** (`recover_within_s`) are expected to breach and must come back inside a deadline. These describe *recovery*.

Rev 1 scored both with one "did it recover" check, which is why a service that never degraded and a service that degraded and recovered scored identically.

### 14.3 Evaluating a hypothesis

```python
# chaos-framework/src/hypothesis/engine.py
@dataclass
class InvariantOutcome:
    name: str
    outcome: str            # held | falsified | invalid
    worst_value: float | None
    threshold: float
    breached_for_s: float
    evidence: dict          # the sample window, so the verdict is auditable

def evaluate(hypothesis, samples: SampleSet) -> HypothesisVerdict:
    if not samples.is_valid(hypothesis.min_rps_floor):        # §16.1 — the gate
        return HypothesisVerdict("invalid", [], reason=samples.invalidity_reason)

    outcomes = []
    for inv in hypothesis.invariants:
        series = samples.series(inv.source, inv.expr)
        if series.is_empty():
            # A missing series is NEVER a passing series. This is the single most
            # common way a chaos framework lies to you.
            outcomes.append(InvariantOutcome(inv.name, "invalid", None, inv.threshold, 0,
                                             {"reason": "no samples for expression"}))
            continue

        if inv.recover_within_s is not None:
            outcomes.append(_check_recovery(inv, series))
        else:
            outcomes.append(_check_hold(inv, series))     # breach duration vs tolerance_s

    if any(o.outcome == "invalid" for o in outcomes):
        return HypothesisVerdict("invalid", outcomes)
    if any(o.outcome == "falsified" for o in outcomes):
        return HypothesisVerdict("falsified", outcomes)
    return HypothesisVerdict("held", outcomes)
```

The rule in the comment is worth restating because it is where most home-grown chaos frameworks quietly break: **an empty PromQL result is not a satisfied condition.** If `resilience4j_circuitbreaker_state` returns nothing because `resilience4j-micrometer` was never added to the classpath (§C.6), a naive validator sees "no failures found" and passes. Rev 2 returns `invalid` and refuses to score.

### 14.4 The rewritten runner

This replaces §5.2. The shape is the contribution — six ordered stages with the safety and validity gates structurally in front of the fault, not optional:

```python
# chaos-framework/src/orchestrator/experiment_runner.py  (Rev 2)
async def run(self, experiment) -> ExperimentResult:
    epoch = await self.epochs.current()                      # §21.1
    bundle = EvidenceBundle(experiment=experiment, epoch=epoch, git_sha=self.git_sha)

    # STAGE 0 — load plane up, steady state established (§16.1)
    load = await self.load.start(experiment.load_profile)
    bundle.load_run = load
    await self.load.await_steady(experiment.min_rps_floor, timeout_s=120)

    # STAGE 1 — pre-flight: health, validity, blast radius, budget gate (§C.5, §15)
    pre = await self.preflight(experiment)
    if not pre.ok:
        await self.load.stop(load)
        return self._record(bundle, verdict=pre.verdict, reason=pre.reason)

    # STAGE 2 — baseline over a window long enough to be meaningful, short enough to matter
    bundle.baseline = await self.sampler.window(experiment, seconds=120)

    # STAGE 3 — inject, with probes armed as in-experiment aborts (§15.3)
    crd = experiment.to_chaos_engine_crd(probes=self.safety.probes_for(experiment))
    injection = await self.litmus.apply(crd)
    bundle.injected_at = injection.started_at

    # STAGE 4 — sample at fixed cadence from BOTH sources while watching abort conditions
    async with self.watchdog.armed(experiment.hypothesis.abort_conditions) as wd:
        bundle.samples = await self.sampler.stream(
            experiment, until=injection.ends_at + experiment.recovery_window_s,
            interval_s=5, on_abort=wd.trip)
    if wd.tripped:
        await self.safety.abort(injection, reason=wd.reason)     # halt fault, run cleanup
        return self._record(bundle, verdict="aborted", reason=wd.reason)

    # STAGE 5 — evaluate hypothesis, then the four legacy checks as sub-evidence
    bundle.hypothesis_verdict = evaluate(experiment.hypothesis, bundle.samples)
    bundle.checks = await self.validators.run_all(experiment, bundle)

    # STAGE 6 — score (only if valid), persist, sign, notify
    bundle.score = self.scorer.calculate(bundle.checks, epoch=epoch)
    await self.load.stop(load)
    await self.cleanup.run(experiment)                        # saga, always
    return await self._finalise(bundle)
```

Note what is *structural* rather than remembered: load starts before pre-flight, pre-flight runs before injection, the watchdog wraps the sampling window, and cleanup runs on every path. A runner that forgets one of these cannot be made safe by discipline in the individual experiments.

---

## 15. The Safety Plane — Blast Radius, Abort-on-Harm, Error-Budget Gating

### 15.1 Why this is the section that makes production chaos possible

Chaos in CI (Rev 1's headline) runs against an ephemeral `kind` cluster, where safety is free — nothing real is at stake. That is also its limitation, and a sharp interviewer will find it: *"so you never actually ran chaos against anything that mattered."* The safety plane is what lets you answer *"I did, and here is the mechanism that made it responsible."*

Three layers, at three different timescales:

| Layer | When | Mechanism | Fails how |
|---|---|---|---|
| **Pre-flight** | Before injection | Blast-radius computation, steady-state check, validity floor, budget gate | Refuses to start — `SKIPPED` or `DENIED` |
| **In-experiment** | During the fault | Litmus probes (`promProbe`, `httpProbe`) with `stopOnFailure`, plus an independent Python watchdog | Halts the fault — `ABORTED` |
| **Post-experiment** | After | Saga cleanup, verification that the cluster returned to its declared state | Escalates and trips the chaos breaker |

Two independent abort paths is deliberate. Litmus probes are in-band and fast but fail with the chaos runner; the Python watchdog is out-of-band and survives a runner crash. **A safety mechanism owned by the process that can crash is not a safety mechanism** — the same reasoning that puts a watchdog behind any compensating action.

### 15.2 Blast radius, computed before injection

```python
# chaos-framework/src/safety/blast_radius.py
@dataclass(frozen=True)
class BlastRadius:
    affected_pods: int
    affected_replica_fraction: float      # affected / total for the target workload
    affected_namespaces: list[str]
    requests_at_risk_per_min: float       # from the CURRENT load plane rate, not a guess
    max_recovery_s: float                 # from historical p95 for this experiment type
    error_budget_burn_pct: float          # share of the 30-day budget this could consume
    user_facing: bool

    def score(self) -> int:
        """Deliberately simple and explainable — a reviewer can evaluate it mentally."""
        return (self.affected_pods
                + 10 * len(self.affected_namespaces)
                + (25 if self.user_facing else 0)
                + int(50 * self.affected_replica_fraction))
```

The budget arithmetic is the part worth being able to derive on a whiteboard:

```
Availability SLO        = 99.5% over 30 days
Total budget            = 0.005 × 30d × 86400 s/d          = 12,960 error-seconds
Historical impact for this experiment (p95):
  degraded availability = 96%  →  error rate 4%
  duration              = 45 s  (fault 60s, but impact starts after endpoint removal)
Budget consumed         = 0.04 × 45                        = 1.8 error-seconds
Share of monthly budget = 1.8 / 12960                      = 0.014%
Daily run × 30          = 0.42% of the monthly budget
```

> The number that lands in an interview: *"My entire daily chaos programme — six experiments, every day, for a month — consumes under half a percent of my availability error budget. I can show that calculation, which is what makes chaos an easy sell instead of a scary one. And the gate is derived from it: any single experiment projected to burn more than 2% of the remaining budget requires an explicit override."*

### 15.3 In-experiment aborts via Litmus probes

Rev 1 used a single `httpProbe` in the pod-kill CRD as a *health check*. Rev 2 uses probes as *abort conditions*, which is a different job:

```yaml
# excerpt from the generated ChaosEngine
probe:
  - name: client-availability-guard
    type: promProbe
    mode: Continuous
    runProperties:
      probeTimeout: 5s
      interval: 5s
      retry: 1
      stopOnFailure: true            # <-- this is the abort
    promProbe/inputs:
      endpoint: http://prometheus-operated.monitoring:9090
      query: |
        1 - (sum(rate(k6_http_reqs_failed_total{testrun="{{TESTRUN}}"}[30s]))
             / sum(rate(k6_http_reqs_total{testrun="{{TESTRUN}}"}[30s])))
      comparator: { type: float, criteria: ">=", value: "0.80" }

  - name: blast-radius-containment
    type: promProbe
    mode: Continuous
    runProperties: { probeTimeout: 5s, interval: 10s, retry: 0, stopOnFailure: true }
    promProbe/inputs:
      endpoint: http://prometheus-operated.monitoring:9090
      query: |
        sum(rate(http_server_requests_seconds_count{status=~"5..",namespace!="target-app"}[30s]))
      comparator: { type: float, criteria: "<=", value: "0.5" }
```

Three implementation notes that cost time if missed:

1. **`mode: Continuous` with `stopOnFailure: true`** is what turns a probe into an abort. `mode: EOT` (end-of-test) only tells you afterwards, which is a validator, not a guard.
2. **Use multiple probes of the same type deliberately, and pin Litmus ≥ 3.28.0** — a stale-config leak across multiple probes of the same type was fixed there, and both probes above are `promProbe`.
3. **Probe queries read k6's exported series**, which is why the load plane must export to Prometheus (§16.2). A guard that reads server-side metrics cannot see the failures that never reached a server.

### 15.4 Safety rules as declarative policy

The rules live in git and are evaluated with `cel-python`, so adding a guardrail is a pull request rather than a code change:

```yaml
# policy/chaos_safety.yaml — first DENY wins, then REQUIRE_OVERRIDE, else ALLOW
rules:
  - id: deny-outside-allowed-namespaces
    effect: deny
    expr: '!(experiment.target_namespace in ["target-app","chaos-staging"])'
  - id: deny-stateful-targets
    effect: deny
    expr: 'experiment.target_kind == "StatefulSet" || experiment.target_name.startsWith("postgres")'
  - id: deny-chaosproof-self
    effect: deny
    expr: 'experiment.target_namespace == "chaosproof"'
  - id: deny-single-replica
    effect: deny
    expr: 'target.replicas < 2'
  - id: deny-budget-gate-closed
    effect: deny
    expr: 'budget.gate_state != "open"'
  - id: deny-below-validity-floor
    effect: deny
    expr: 'load.current_rps < experiment.min_rps_floor'
  - id: require-override-large-radius
    effect: require_override
    expr: 'blast.score() > 60'
  - id: require-override-budget-heavy
    effect: require_override
    expr: 'blast.error_budget_burn_pct > 2.0'
```

**ChaosProof refuses to inject chaos into ChaosProof.** That rule deserves its own sentence in the interview: a framework that can kill the pod holding the experiment's state loses the experiment it was running, and the resulting half-written execution row is worse than no data. The exclusion is in the policy file, not in code.

### 15.5 The error-budget gate — the SRE position, enforced

This closes D6. The budget is not decoration; it is an admission-control input with three states:

```python
# chaos-framework/src/safety/budget_gate.py
BUDGET_WARN_PCT   = 50.0      # half the 30-day budget spent
BUDGET_FREEZE_PCT = 80.0

def gate(namespace: str) -> Gate:
    spent = budget.spent_pct(namespace, window_days=30)     # ALL burn, not just chaos
    if breaker.is_open(namespace):
        return Gate("closed", f"chaos breaker open: {breaker.reason(namespace)}")
    if spent >= BUDGET_FREEZE_PCT:
        return Gate("closed",
                    f"{spent:.0f}% of the 30-day error budget is spent — "
                    "reliability work takes priority over injecting more failure")
    if spent >= BUDGET_WARN_PCT:
        return Gate("restricted", f"{spent:.0f}% spent — read-only and low-radius experiments only")
    return Gate("open", f"{spent:.0f}% spent")
```

The **chaos breaker** trips on any of: 3 aborts in 24 hours (the system keeps behaving worse than predicted), 2 consecutive `INVALID` verdicts for the same experiment (the measurement plane is broken and results are meaningless), a cluster-wide Kubernetes API error rate above 20%, or a manual `chaosctl freeze --reason "release in progress"`. Recovery is `open → half_open` after 6 hours, where exactly one low-radius experiment is allowed through and its outcome decides `closed` or `open` again.

> This is the answer to the hardest safety question, and the framing matters: *"An error budget isn't just something I report — it's an input to whether chaos runs at all. If 80% of the 30-day budget is already spent, my scheduler will not inject more failure, because at that point the right engineering decision is to fix reliability, not to keep proving it's broken. That's the Google SRE Workbook position on error budget policy, and it's enforced in code rather than described in a doc. The subtlety is that the gate counts *all* budget burn — real incidents included — not just what my own experiments consumed."*

### 15.6 Cleanup as a saga

Registered **before** injection, executed on every exit path including abort and crash:

```python
CLEANUP_STEPS = [
    ("delete_chaosengine",   lambda e: litmus.delete(e.crd_name)),
    ("restore_feature_flags",lambda e: flags.restore(e.flag_snapshot)),      # §17
    ("restore_replicas",     lambda e: k8s.scale_to(e.target, e.replica_snapshot)),
    ("resume_gitops",        lambda e: gitops.resume(e.target)),
    ("stop_load",            lambda e: load.stop(e.load_run_id)),
    ("verify_steady_state",  lambda e: steady_state.assert_holds(e.hypothesis, window_s=120)),
]
```

A `chaos-cleanup` CronJob runs every 5 minutes as the out-of-band path: any `experiment_executions` row in `running` state whose `started_at` is older than its configured maximum gets force-cleaned, and `chaosproof_orphaned_cleanups_total` is alerted on above zero. The final step is the important one — **cleanup is not done until steady state is re-established**, and if it cannot be, that escalates rather than closing quietly.

---

## 16. Measurement Integrity — the Load Plane, SLI Validity, Honest Recovery Timing

This section fixes D1 and D2. It is the least glamorous work in Rev 2 and the reason every other number in the project can be defended.

### 16.1 The load plane

**k6 2.2.x, run in-cluster by k6 Operator v1.0 as a `TestRun` CR.** Not from a laptop, not from the GitHub runner: the load must originate inside the cluster so that the network path under test is the real one, and it must outlive the CI job.

```javascript
// profiles/steady_120rps.js
import http from 'k6/http';
import { Trend, Rate } from 'k6/metrics';

export const options = {
  discardResponseBodies: true,
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',   // OPEN model — see §16.2
      rate: 120, timeUnit: '1s',
      duration: '10m',
      preAllocatedVUs: 200, maxVUs: 800,   // headroom so we never become the bottleneck
    },
  },
  thresholds: {
    // These are k6's own pass/fail, independent of Prometheus. A discrepancy is a finding.
    'http_req_failed':   ['rate<0.01'],
    'http_req_duration': ['p(99)<800'],
    'dropped_iterations':['count<10'],     // if k6 can't keep the rate, the run is INVALID
  },
});

export default function () {
  http.get(`${__ENV.TARGET_URL}/api/orders/checkout`, { tags: { endpoint: 'checkout' } });
}
```

**The validity gate.** A verdict is only scoreable if the measurement was capable of detecting failure:

```python
MIN_SAMPLE_COVERAGE = 0.90       # fraction of the window with samples present

def is_valid(self, min_rps_floor: float) -> bool:
    if self.achieved_rps < min_rps_floor:
        self.invalidity_reason = (f"achieved {self.achieved_rps:.0f} rps, floor is "
                                  f"{min_rps_floor:.0f} — cannot measure availability")
        return False
    if self.dropped_iterations > DROPPED_CEILING:
        self.invalidity_reason = ("k6 could not sustain the arrival rate — the load "
                                 "generator was the bottleneck, not the system")
        return False
    if self.coverage < MIN_SAMPLE_COVERAGE:
        self.invalidity_reason = f"only {self.coverage:.0%} sample coverage in the window"
        return False
    return True
```

`dropped_iterations` deserves attention: under an open model, if k6 cannot start iterations at the requested rate it reports drops. Drops mean **the load generator saturated before the system did**, so any conclusion about the system is unfounded. Rev 1 had no way to detect this because it had no load generator at all; a naive addition of k6 with `constant-vus` would have hidden it permanently.

### 16.2 Open vs closed workload model — the second documented scaling decision

> **Decision: an open workload model (constant arrival rate) over a closed one (constant VUs).**
>
> **Problem:** k6's default `constant-vus` executor holds a fixed number of virtual users, each sending a request, waiting for the response, then sending the next. This is a **closed model**, and it has a property that is fatal for chaos measurement: **when the system slows down, offered load automatically falls.** A pod kill that doubles latency halves throughput, so the fault partially hides itself — fewer requests are in flight to fail, error *count* drops, and the availability *ratio* can even improve while users are having a worse time.
>
> **Option A — closed model (`constant-vus`).** Simpler, self-limiting, cannot overwhelm the target. Rejected: it couples offered load to system health, which is precisely the variable under test. It also makes runs incomparable, because two runs at "50 VUs" are two different load levels if latency differed.
>
> **Option B — open model (`constant-arrival-rate`).** Requests are launched on a schedule regardless of whether earlier ones finished; queues build under stress, exactly as real traffic does. **Chosen.**
>
> **Trade-off accepted:** an open model *can* overwhelm the target, and it needs `preAllocatedVUs`/`maxVUs` headroom plus `dropped_iterations` monitoring to distinguish "the system is failing" from "the generator is failing." That is more configuration and one more failure mode to watch — and it is the price of a measurement that does not flatter the system.
>
> **When I would use a closed model:** capacity planning, where the question is "how many concurrent users can this serve?" rather than "what happens to a fixed traffic stream when I break something?"

This is the second-strongest interview moment in the project after the CI gate, because almost nobody who has run a load test can explain the distinction, and it is the difference between a measurement and a decoration.

### 16.3 Recovery timing that is physically possible

Rev 1's queries used `rate(...[5m])` and `histogram_quantile(..., [5m])` while asserting a 30-second recovery SLO. A 5-minute rate window is a 5-minute moving average: after an instantaneous full recovery it needs minutes to return to baseline. **The measurement lag exceeded the thing being measured by an order of magnitude.**

Four changes:

| Change | From | To | Why |
|---|---|---|---|
| Target-app scrape interval | 30s (chart default) | **5s** on the target `ServiceMonitor` only | A 30s window needs ≥2 samples; 5s scrape gives 6 |
| SLI rate window | `[5m]` | **`[30s]`** during experiments | Shortest window that is statistically meaningful at 5s scrape |
| Latency percentile | `histogram_quantile(0.99, ...[5m])` | k6 client-side p99, corroborated by **native histograms** where available | Server-side quantiles over long windows cannot resolve a 30s event |
| **Primary recovery clock** | Prometheus | **k6 time series (1s granularity)** | The client knows when it started succeeding again; Prometheus knows ~5s later |

Raising scrape frequency only for the target app is the right scoping — cluster-wide 5s scraping multiplies Prometheus load for no benefit. Say that out loud; it shows you understand the cost of your own instrumentation.

**And the deeper point (§16.2's companion): server-side metrics structurally under-report pod kills.** When a pod is deleted, in-flight and newly-arriving requests to that endpoint fail at the TCP or HTTP layer *before reaching any application*. Those failures never increment `http_server_requests_seconds_count` — the counter only exists inside a running server. So server-side availability computed as `non-5xx / total` is biased **upward** during exactly the fault this project leads with. Client-side k6 metrics are the honest denominator; the server-side series is corroboration and a source of a genuinely interesting third signal:

```
client_error_rate - server_error_rate  ≈  requests that never reached a server
```

> Interview soundbite: *"The subtle bug is that server-side metrics can't see a request that never reached a server. When I kill a pod, connections in flight fail at the transport layer and never increment any application counter, so server-side availability actually looks *better* during a pod kill than reality. I compute availability from the client's perspective with k6, use Prometheus as corroboration, and I treat the gap between them as its own signal — it's a direct estimate of the requests that died before they were ever served."*

### 16.4 What "recovered" means

Three conditions, all required:

1. **Client-observed SLI restored** — k6 error rate back below threshold and holding for 30 consecutive seconds (not a single sample, which flaps).
2. **Golden signals recovered** — no new restarts, CPU throttle *ratio* within ceiling (dimensionless: `rate(container_cpu_cfs_throttled_periods_total[30s]) / rate(container_cpu_cfs_periods_total[30s])` — raw throttled-period counters move with replica count and window length and would make the check meaningless), PSI full-stall share within ceiling, and `kube_deployment_status_replicas_available` back at target.
3. **No new alert on the target that the fault could have caused** — a fix that starts a different problem is not a recovery.

If signals are unavailable → `INVALID` and escalate. Never a silent success.

### 16.5 Alert-latency measured honestly

Rev 1's check: "did Prometheus alert within 60 seconds of fault start?" As written, this mostly grades your own Prometheus configuration, because a large part of the delay is irreducible:

```
irreducible_latency = scrape_interval + evaluation_interval + rule `for:` duration
                    = 5s              + 30s                 + 60s              = 95s
```

With a `for: 1m` rule, a 60-second target is **unachievable by construction** — the alert cannot fire before 95 seconds no matter how good the system is. Rev 1 would have scored a correctly-configured alerting stack as a failure, every single run.

The fix is to report two numbers and grade the second:

```python
detection_latency  = alert_fired_at - fault_injected_at          # what happened
irreducible        = scrape_interval + evaluation_interval + rule_for_duration
excess_latency     = detection_latency - irreducible             # what I control

# Grade excess against the SLO; report both. And validate the config itself:
assert rule_for_duration + evaluation_interval + scrape_interval <= slo_alert_latency_s, \
    "alert rule cannot possibly meet its own latency SLO — fix the rule, not the system"
```

That assertion is a CI check on the alert rules (§21.5), and it is a genuinely useful piece of tooling: **it catches alert SLOs that are arithmetically impossible before anyone relies on them.**

> *"My alert-latency check subtracts the irreducible detection lag — scrape interval plus evaluation interval plus the rule's `for` duration — and grades only the excess. Otherwise I'm grading my own Prometheus config instead of the system, and worse, my original 60-second target was mathematically unreachable with a one-minute `for` clause. I turned that into a CI assertion: if a rule's floor exceeds its own latency SLO, the build fails and the rule gets fixed."*

### 16.6 Dashboard panels this section adds

Hypothesis verdict per experiment with the falsified invariant named · **SLI-validity strip** showing achieved rps against the floor, coloured red for `INVALID` runs · client-vs-server availability overlay with the gap shaded (the "requests that never reached a server" estimate) · recovery timeline with fault, first-failure, alert-fired, irreducible-latency marker, and SLI-restored as distinct events · `dropped_iterations` as a load-generator health indicator · error-budget gauge with the gate state (`open` / `restricted` / `closed`).

---

## 17. Counterfactual Resilience Analysis

### 17.1 The idea

Every chaos tool proves a system survived. None of them prove *what made it survive*. Counterfactual chaos runs the same fault twice — once with a resilience pattern enabled, once with it disabled — and reports the delta. That converts "we have a circuit breaker" into "this circuit breaker prevents ~5,000 failed requests per incident of this class."

The mechanism is a **feature-flag plane** in the target app. Resilience4j instances can be disabled at runtime, so the runner flips the flag, re-runs the identical experiment, and diffs the SLI results.

### 17.2 Statistical honesty — the part the original feature sketch got wrong

The features document showed a single with/without pair producing "$253 per incident." One run per arm cannot support that, because chaos experiments are noisy: pod scheduling, endpoint propagation, and JIT warmup all vary between runs. A single-pair delta is an anecdote wearing a number's clothes.

Rev 2 requires **n ≥ 5 repetitions per arm**, interleaved (`with, without, with, without, …` rather than all-with-then-all-without, so drift in cluster conditions affects both arms equally), and reports a distribution:

```python
# chaos-framework/src/counterfactual/analysis.py
MIN_REPETITIONS = 5

@dataclass
class CounterfactualResult:
    pattern: str
    n: int
    with_median_failed: float;    with_iqr: tuple[float, float]
    without_median_failed: float; without_iqr: tuple[float, float]
    delta_median: float
    overlap: bool                 # do the IQRs overlap? if so, say so loudly
    verdict: str                  # "pattern_effective" | "no_measurable_effect" | "inconclusive"

def analyse(runs) -> CounterfactualResult:
    ...
    if iqr_overlap(with_iqr, without_iqr):
        verdict = "inconclusive"   # NOT "pattern_effective with a smaller number"
```

**When the interquartile ranges overlap, the honest verdict is `inconclusive`.** Reporting a median delta from overlapping distributions is how dashboards become fiction. This restraint is itself the interview signal — anyone can produce a big number; being able to say "my n was too small to claim that" is what a senior engineer listens for.

Cost translation stays, with its assumptions attached in the UI rather than buried:

```
Assumptions (editable, shown on the panel):
  revenue per successful checkout   = ₹450        [assumption]
  conversion loss per failed request = 100%       [assumption — pessimistic]
  incidents of this class per year   = 12         [from 6 months of incident data, extrapolated]
```

Label estimates as estimates. *"₹3,000/year, under these three assumptions which are on the dashboard"* is credible; an unqualified rupee figure invites an interviewer to dismantle it.

### 17.3 Safety constraints specific to this feature

Deliberately disabling a resilience pattern is the most dangerous thing ChaosProof does — the "without" arm is *designed* to fail harder.

- **Counterfactual runs are restricted to `chaos-staging` by policy** (§15.4), never the demo-prod namespace.
- The **flag snapshot is captured before the run and restored by the cleanup saga** (§15.6), with the CronJob as the out-of-band path. A cluster left with its circuit breakers disabled is the worst possible outcome of this project.
- The "without" arm gets a **tighter abort threshold** than the "with" arm, because harder failure is expected but unbounded failure is not.
- The dashboard marks counterfactual executions distinctly and **excludes them from the resilience score** — they are deliberately-degraded configurations and would poison the trend.

> Interview soundbite: *"The panel I'm proudest of is the counterfactual one. For each resilience pattern I run the same fault with it on and off, five times each, interleaved — and I report medians with interquartile ranges, not a single delta, because a single pair of chaos runs is noise. When the ranges overlap I report 'inconclusive' rather than a smaller number. It's the closest I can get to an A/B test for reliability engineering, and it's how I'd justify the maintenance cost of a circuit breaker to someone holding a budget."*

---

## 18. Cascading Failure Scenarios

### 18.1 The gap

Every experiment in Rev 1 injects **one** fault. Real outages are cascades: a cache eviction raises database load, which raises latency, which exhausts a connection pool, which times out a caller, which retries, which amplifies the load. Chaos Monkey cannot express this. A `chaos_scenario` — a DAG of faults with temporal and *conditional* triggers — can.

### 18.2 The scenario schema

```yaml
# scenarios/cache_outage_cascade.yaml
scenario:
  name: cache_outage_cascade
  description: >
    Redis becomes unavailable, forcing inventory-service onto the database. If DB latency
    crosses 200ms we additionally add 100ms of network latency, simulating the contention
    a real cache stampede produces. Tests whether the stale-cache fallback and the DB
    retry budget together prevent a user-visible outage.

  hypothesis:
    description: >
      The system degrades gracefully: client availability stays >=97% and P99 stays
      under 1500ms throughout, because inventory-service serves stale catalogue data
      and order-api's bulkhead prevents thread-pool exhaustion from spreading.
    invariants:
      - { name: availability, source: k6, expr: "1 - (http_req_failed/http_reqs)",
          comparator: ">=", threshold: 0.97, tolerance_s: 20 }
      - { name: p99, source: k6, expr: "http_req_duration_p99_ms",
          comparator: "<=", threshold: 1500, tolerance_s: 30 }
      - { name: no_thread_pool_exhaustion, source: prometheus,
          expr: "min(resilience4j_bulkhead_available_concurrent_calls)",
          comparator: ">", threshold: 0, tolerance_s: 0 }   # zero tolerance: this is the cascade

  stages:
    - id: kill_cache
      at: 0s
      fault: pod-delete
      target: { app: redis, namespace: target-app }

    - id: db_pressure
      after: kill_cache
      trigger:                       # CONDITIONAL, not just temporal
        promql: histogram_quantile(0.99, sum(rate(db_query_seconds_bucket[30s])) by (le))
        comparator: ">"
        threshold: 0.200
        timeout_s: 90                # if it never trips, record "cascade did not propagate"
      fault: pod-network-latency
      target: { app: inventory-service }
      params: { latency_ms: 100 }

    - id: observe_only
      after: db_pressure
      duration: 60s
      fault: none                    # watch amplification without adding to it

  abort_conditions:
    - { name: total_collapse, source: k6, expr: "1 - (http_req_failed/http_reqs)",
        comparator: "<", threshold: 0.70 }
    - { name: cascade_escaped, source: prometheus,
        expr: 'sum(rate(http_server_requests_seconds_count{status=~"5..",namespace!="target-app"}[30s]))',
        comparator: ">", threshold: 0.5 }
```

Two design decisions worth defending:

- **Conditional triggers, not only delays.** `after: X, trigger: <promql crosses threshold>` means the second fault fires when the cascade actually propagates, not on a stopwatch. If the trigger never trips within `timeout_s`, that is a **result worth recording**: *"the cascade did not propagate — the stale-cache fallback absorbed the cache outage completely."* That is the single most valuable output this feature produces, and a delay-only DAG cannot produce it.
- **A stage with `fault: none`.** Amplification effects (retry storms, queue growth) show up *after* injection stops. An observation-only stage is where you catch a retry storm that a fault-then-immediately-validate loop misses entirely.

### 18.3 Why cascades need the safety plane most

A DAG multiplies blast radius, and the abort path becomes non-trivial: aborting stage 2 while stage 1 is still active must halt **both**, in reverse dependency order, and the cleanup saga runs stage cleanups in reverse too. Build §15 before building §18; a cascade simulator without an abort mechanism is the one feature in this plan that could genuinely take down a cluster.

> *"Real outages are cascades, so one of my experiments is a fault DAG with conditional triggers: I kill the cache, and the second fault only fires if database P99 actually crosses 200 milliseconds. The interesting outcome is when it doesn't fire — that's my stale-cache fallback absorbing the outage completely, and I can prove it rather than assume it. And I built the abort plane first, because a cascade you can't stop isn't an experiment."*

---

## 19. Service Resilience Contracts

### 19.1 The idea

`order-api` depends on `payment-service`, but nowhere is it written what tolerance each expects from the other. When payment-service gets slow, whose bug is it? A contract makes the assumption explicit **and testable** — consumer-driven contract testing (Pact) applied to failure rather than to schemas.

```yaml
# target-app/order-api/resilience-contract.yaml
service: order-api
version: 1.2.0

provides:                                # what I promise my consumers
  availability_slo: 99.5
  latency_p99_ms: 500
  graceful_degradation_modes:
    - "stale-inventory-data-on-cache-miss (max staleness 60s)"
    - "queued-payment-on-payment-svc-failure (max queue depth 1000)"

tolerates:                               # what I absorb from my dependencies
  - dependency: payment-service
    max_latency_ms: 800
    max_error_rate_pct: 5
    max_outage_seconds: 120
  - dependency: inventory-service
    max_latency_ms: 1000
    max_error_rate_pct: 10
    max_outage_seconds: 300
  - dependency: redis
    max_outage_seconds: 600

does_not_inflict:                        # consumer-side guarantees
  - "request rate > 100 rps on any single dependency"
  - "concurrent connections > 50 per dependency"
```

### 19.2 Contracts generate experiments

Each `tolerates` clause is a hypothesis, so the generator emits one experiment per clause — which is the feature's real power: **the experiment suite is derived from declared architecture, not from a human remembering to write a test.**

```python
# chaos-framework/src/contracts/generator.py
def generate(contract) -> list[Experiment]:
    out = []
    for tol in contract.tolerates:
        # "I tolerate 800ms from payment-service" -> inject exactly 800ms, assert MY SLO holds
        out.append(Experiment(
            name=f"contract_{contract.service}_tolerates_{tol.dependency}_latency",
            litmus_fault="pod-network-latency",
            target={"app": tol.dependency},
            params={"latency_ms": tol.max_latency_ms},
            hypothesis=Hypothesis(
                description=(f"{contract.service} claims it tolerates {tol.max_latency_ms}ms "
                             f"from {tol.dependency}; its own P99 SLO must still hold"),
                invariants=[Invariant("consumer_p99_holds", source="k6",
                                      expr="http_req_duration_p99_ms",
                                      comparator="<=",
                                      threshold=contract.provides.latency_p99_ms,
                                      tolerance_s=15)]),
            provenance=ContractRef(contract.service, contract.version,
                                   f"tolerates.{tol.dependency}.max_latency_ms")))
        # ... and one per max_error_rate_pct and max_outage_seconds
    return out
```

The report reads as architecture review rather than test output:

```
CONTRACT VALIDATION — order-api v1.2.0            epoch 7f2a…  git 4c19ba2
  tolerates payment-service latency <= 800ms   -> P99 stayed 480ms   HONOURED
  tolerates payment-service errors   <= 5%     -> own errors 12.1%   VIOLATED
        └─ retry budget amplifies: 3 attempts x 5% upstream = 14% effective. Reduce
           maxAttempts to 2 or add a retry budget cap.
  tolerates redis outage             <= 600s   -> not tested         UNTESTED
  does_not_inflict rps <= 100                  -> peak 96 rps        HONOURED
```

Two properties that make this more than a nice YAML file: **`UNTESTED` is reported**, so contract coverage is itself a metric (`contract_clauses_tested / total_clauses`) and gaps are visible rather than invisible; and a **violated clause names the mechanism** where it can — the retry-amplification arithmetic above is the kind of finding that makes an interviewer sit forward, because it is a real distributed-systems failure mode (retries turning a 5% upstream error rate into 14% observed) rather than a threshold breach.

### 19.3 The CI hook

A PR that **weakens a contract** — raising `max_error_rate_pct`, dropping a degradation mode — requires the generated experiment to still pass, and the diff is surfaced in the PR:

```
CONTRACT DIFF  order-api 1.2.0 -> 1.3.0
  ~ tolerates.payment-service.max_error_rate_pct: 5 -> 15
    ⚠️  This weakens a published guarantee. Reviewers: @order-api-owners @sre
    Chaos gate re-ran the clause at 15%: order-api errors 4.2% -> HONOURED at new threshold.
    But the previous threshold (5%) is what consumers were told. Is the change intentional?
```

That last line is the point: **the gate does not block a legitimate weakening, it makes it visible to the people who relied on it.** Tooling that forces a conversation is more useful than tooling that forces a merge failure.

> Interview soundbite: *"Each service declares a resilience contract — what it tolerates from each dependency and what it promises its consumers — and ChaosProof generates one experiment per clause to validate the claim. It's consumer-driven contract testing applied to failure instead of to schemas. The best finding it produced was a retry-amplification bug: order-api claimed it tolerated a 5% error rate from payment-service, but with three retry attempts a 5% upstream rate became 14% observed. The contract was violated by its own retry policy, and no schema test would ever have found that."*

---

## 20. Resilience Regression Bisection

### 20.1 The idea

The dashboard shows the score dropped from 87% to 71% over a week. *Which commit did that?* Bisection replays the identical experiment across the commit range, binary-searching for the regression — `git bisect` for reliability.

```python
# chaos-framework/src/bisect/runner.py
async def bisect(experiment_type: str, good_sha: str, bad_sha: str,
                 reps: int = 3) -> BisectResult:
    """
    Binary search over commits, running the SAME experiment (same epoch, same load
    profile, same hypothesis version) at each candidate.
    """
    lo, hi = commit_index(good_sha), commit_index(bad_sha)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        verdict = await self._evaluate_candidate(experiment_type, commit_at(mid), reps)
        if verdict == "inconclusive":
            return BisectResult.abandoned(
                commit_at(mid), reason="candidate straddles the flakiness band")
        lo, hi = (mid, hi) if verdict == "good" else (lo, mid)
    return BisectResult.found(commit_at(hi))
```

### 20.2 Why naive bisection converges on noise

This is the part the original feature sketch skipped, and it is what makes the difference between a demo and a tool. Chaos experiments have run-to-run variance. If the score's standard deviation on unchanged code is σ = 0.06 and the regression is 0.16, a single run at each candidate has a meaningful chance of misclassifying — and **one misclassification sends the binary search down the wrong half permanently.**

So bisection depends on §21.3's flakiness measurement:

```
Required separation:  |score_good − score_bad|  >  2 × (σ_good + σ_bad)
With σ = 0.06 and a 0.16 regression:   0.16 > 2 × 0.12 = 0.24   →  FALSE
   ⇒ 1 repetition is insufficient. Use the mean of r reps: σ_mean = σ / √r
   With r = 3:  σ_mean = 0.035  →  0.16 > 2 × 0.07 = 0.14  →  TRUE
```

If a candidate's mean lands inside the flakiness band, the correct behaviour is to **abandon the bisection and say so**, not to guess. `BisectResult.abandoned` is a first-class outcome.

### 20.3 The third documented scaling decision

> **Decision: bounded bisection with pre-computed cost, over unbounded search.**
>
> **Problem:** each candidate costs `reps × (load_warmup + fault + recovery + cleanup)` ≈ 3 × 5 min = 15 min. A 40-commit range needs `log₂(40) ≈ 6` candidates → **~90 minutes** of cluster time.
>
> **Option A — bisect on every score regression, automatically.** Rejected: a 90-minute cluster-monopolising job triggered by noise is a denial of service against the daily chaos schedule.
>
> **Option B — bisect on demand, with a cost estimate shown before starting, a hard candidate cap, and a nightly window.** Chosen. The Slack message reporting a score regression carries a *button*: "Bisect (est. 6 candidates, ~90 min)". A human decides.
>
> **Trade-off accepted:** regressions are not diagnosed instantly. In exchange, the daily schedule is never starved, and the cost is visible before it is incurred rather than discovered afterwards.

---

## 21. The DevOps/MLOps Wrapper — Scoring Epochs, Replay Evals, Flakiness Quarantine

The brief asked for a DevOps/MLOps wrapper. Rev 1 had none — this section is a completeness gap, not a nice-to-have. The framing that makes it coherent: **ChaosProof's scorer is a deterministic model that maps evidence to a number, so it gets the treatment a model gets** — versioned artefact, labelled dataset, offline eval gating CI, drift monitoring, and rollback.

| MLOps practice | ChaosProof equivalent |
|---|---|
| Versioned model artefact | **Scoring epoch** — `sha256(weights ‖ experiment set ‖ SLO version ‖ scorer version)`, stamped on every execution |
| Labelled dataset | Committed **evidence bundles** with expected verdicts, generated by real runs |
| Offline evaluation gating CI | `evals/replay_eval.py` — replays bundles through the scorer, asserts verdicts and determinism |
| Data validation | The **SLI-validity gate** (§16.1) — refuse to score unmeasurable input |
| Shadow deployment | New experiments run **advisory-only** until their flakiness is characterised |
| Progressive rollout | Advisory → gating promotion, per experiment |
| Drift monitoring | Flakiness (σ of score on unchanged `git_sha`) and verdict-flip rate, exported to Prometheus |
| Rollback | Epoch rollback + `chaosctl freeze` |
| Model card | `EXPERIMENTS.md` — each experiment's hypothesis, fault, blast radius, abort conditions, flakiness σ, and gating status |

### 21.1 Scoring epochs — fixing D4

A resilience score is only comparable to another score computed the same way. Rev 1's 45%→92% story spans four weeks in which alert rules were added (changing what `alert_validation` could pass) and experiments were tuned. Some of that improvement was the system; some was the scorer.

```python
def current_epoch() -> Epoch:
    material = canonical_json({
        "weights":        WEIGHTS,
        "experiment_set": sorted(registry.gating_experiment_names()),
        "slo_version":    slos.version,
        "scorer_version": SCORER_VERSION,
    })
    return Epoch(sha256=hashlib.sha256(material.encode()).hexdigest(), ...)
```

Three consequences:

1. **The trend chart only connects points within an epoch.** Epoch boundaries render as a labelled vertical line with the change reason. Two segments, honestly separated, are far more persuasive than one smooth line a reviewer can dismantle.
2. **Retro-scoring.** Because raw observations live in `sli_samples` and the bundle, changing the scorer triggers a re-score of the last N stored runs under the new epoch, producing a *comparable* history. This is the entire reason the raw samples are persisted rather than just the score.
3. **The 45%→92% story survives, told correctly:** *"Four weeks, one scoring epoch after I retro-scored the early runs. The weights changed once in week two when I added alert validation — that boundary is on the chart, and the earlier runs were re-scored under the new weights so the comparison is real. Without that, some of my improvement would have been me editing the scorer, and I'd rather show you the seam than pretend it isn't there."*

### 21.2 The replay eval — a CI gate, not a unit test

```python
# evals/replay_eval.py
"""
Replays committed evidence bundles through the scorer and hypothesis engine.
Hermetic: no cluster, no Prometheus, no network. Runs on every PR in seconds.
"""
TARGET_ACCURACY = 1.00      # verdicts are deterministic: any mismatch is a bug

for case in corpus:                                   # >= 20 bundles
    verdict = engine.evaluate(case.hypothesis, SampleSet.from_bundle(case.bundle))
    score   = scorer.calculate(case.checks, epoch=case.epoch)
    assert verdict.outcome == case.expected_verdict, case.name
    assert abs(score.value - case.expected_score) < 1e-9, case.name
    digests.append(sha256_canonical({"verdict": verdict, "score": score}))

assert digests == frozen_digests[SCORER_VERSION]      # determinism anchor
```

The corpus must include the cases that catch the Rev 1 defects, which is how you prove a fix stays fixed:

| Case | Asserts |
|---|---|
| `no_load_zero_traffic` | Verdict is **`invalid`**, not `held` — the D1 regression test |
| `pattern_not_applicable` | Weights **renormalise** to 0.75; score is 0.588, not 0.625 — the D3 regression test |
| `empty_promql_series` | Missing series → `invalid`, never a pass — the §14.3 rule |
| `alert_for_exceeds_slo` | Config assertion fails and names the rule — §16.5 |
| `dropped_iterations_high` | Load generator saturated → `invalid` |
| `abort_mid_experiment` | Verdict `aborted`, score `None`, cleanup recorded |
| `epoch_mismatch` | Scoring a bundle under a different epoch is **refused**, not silently coerced |

Two more gates in the same CI stage: **`evals/policy_eval.py`** (every safety rule in §15.4 has a must-allow and a must-deny case) and **`evals/alert_rule_lint.py`** (the §16.5 arithmetic — every alert rule's irreducible latency must be under its own SLO).

### 21.3 Flakiness quarantine — why anyone would trust the CI gate

This is the most practically important thing in the section, and it is the honest answer to the strongest objection to chaos-in-CI: **a flaky gate gets disabled.** If a chaos check fails randomly on innocent PRs, developers will route around it within a week — with `--no-verify`, with an admin merge, or by deleting the workflow. Every CI gate that has ever been abandoned was abandoned for this reason.

So ChaosProof measures its own variance, on unchanged code, and demotes experiments that cannot gate reliably:

```python
# chaos-framework/src/quality/flakiness.py
FLAKINESS_WINDOW      = 20      # runs on unchanged git_sha
MAX_SCORE_STDDEV      = 0.05
MAX_VERDICT_FLIP_RATE = 0.05    # verdict flips with no code change

def evaluate(experiment_type: str) -> FlakinessVerdict:
    runs = db.runs_on_unchanged_sha(experiment_type, limit=FLAKINESS_WINDOW)
    if len(runs) < FLAKINESS_WINDOW:
        return FlakinessVerdict("uncharacterised", gating=False)   # advisory until proven

    sigma = stdev(r.score for r in runs if r.score is not None)
    flips = flip_rate(r.verdict for r in runs)

    if sigma > MAX_SCORE_STDDEV or flips > MAX_VERDICT_FLIP_RATE:
        return FlakinessVerdict("flaky", gating=False,
            reason=f"sigma={sigma:.3f} flips={flips:.1%} — cannot block a merge on this")
    return FlakinessVerdict("stable", gating=True)
```

Three rules that follow, and they are the load-bearing ones:

1. **A new experiment is advisory-only until characterised.** It reports, comments on PRs, and cannot block. It earns gating status after 20 clean runs — progressive delivery applied to your own tooling.
2. **A flaky experiment is auto-quarantined** to advisory, with a Slack notice naming σ, and a GitHub issue opened to fix the *experiment*. Quarantine is not deletion; the experiment keeps running and reporting.
3. **σ feeds bisection** (§20.2). The flakiness band is what makes bisection's stopping condition principled rather than arbitrary.

> Interview soundbite: *"The hardest problem with chaos in CI isn't building the gate — it's that a flaky gate gets disabled. So ChaosProof measures its own variance: it runs each experiment repeatedly against unchanged code, computes the standard deviation of the score and the verdict flip rate, and any experiment that can't hold σ under 0.05 is automatically demoted to advisory and files an issue against itself. A new experiment starts advisory and earns the right to block a merge after twenty clean runs. That's progressive delivery applied to my own tooling, and it's the reason a team would still have this gate enabled six months later."*

### 21.4 Drift and self-monitoring

```
chaosproof_experiment_score{experiment,epoch}
chaosproof_verdict_total{experiment,verdict="held|falsified|invalid|skipped|denied|aborted"}
chaosproof_sli_validity_ratio{experiment}          # valid runs / total runs
chaosproof_experiment_score_stddev{experiment}     # the flakiness signal
chaosproof_gating_experiments                      # how many can actually block a merge
chaosproof_error_budget_spent_ratio{namespace}
chaosproof_budget_gate_state{namespace}            # 0 open, 1 restricted, 2 closed
chaosproof_chaos_breaker_state                     # 0 closed, 1 half_open, 2 open
chaosproof_orphaned_cleanups_total
chaosproof_contract_clause_coverage_ratio{service}
chaosproof_client_server_availability_gap{experiment}   # §16.3's third signal
```

Litmus itself gained Prometheus metrics in 3.29.0, so the chaos platform is observable alongside the target — worth wiring, because "the chaos framework failed silently" is otherwise indistinguishable from "everything passed."

### 21.5 The CI pipeline, Rev 2 shape

```yaml
jobs:
  unit:                      # pure scorer / hypothesis / validator logic, no cluster
    - run: pytest -m "not chaos" --cov=chaos-framework --cov-fail-under=80

  replay-eval:               # §21.2 — hermetic, seconds, catches every fixed defect
    - run: python -m evals.replay_eval
    - run: python -m evals.policy_eval
    - run: python -m evals.alert_rule_lint        # §16.5 impossible-SLO check

  contract-gate:             # §19.3 — surfaces weakened guarantees for review
    - run: python -m chaos_framework.contracts.diff --base ${{ github.base_ref }}

  chaos-gate:                # the headline: real kind cluster, real faults, real load
    - uses: helm/kind-action@v1
      with: { node_image: "kindest/node:v1.36.0" }
    - run: helm install kps prometheus-community/kube-prometheus-stack --version <PINNED>
    - run: helm install litmus litmuschaos/litmus --version <PINNED>
    - run: helm install target-app charts/target-app --set replicas=3
    - run: kubectl apply -f k6/testrun-ci.yaml     # LOAD FIRST — else the gate proves nothing
    - run: python -m chaos_framework.orchestrator.ci_runner --experiments pod_kill,net_latency
    - if: failure()
      run: kubectl cluster-info dump --output-directory=/tmp/dump

  build-sign-deploy:
    needs: [unit, replay-eval, contract-gate, chaos-gate]
    - run: docker buildx build --tag ghcr.io/OWNER/chaosproof:${{ github.sha }} --push .
    - uses: sigstore/cosign-installer@v3
    - run: cosign sign --yes ghcr.io/OWNER/chaosproof@${DIGEST}
    - run: helm upgrade --install chaosproof charts/chaosproof --wait --atomic
```

The ordering inside `chaos-gate` is the correction that matters: **load starts before the experiment runner.** A CI chaos gate with no load plane is the D1 defect reproduced inside the pipeline, where it is hardest to notice — the job goes green, the badge says the resilience gate passed, and nothing was ever measured.

Also note `--cov-fail-under=80` on the scorer specifically. The scorer is the one component whose bugs are invisible: a wrong number looks exactly like a right number.

---

## 22. Postmortem-as-Code and Signed Evidence Bundles

### 22.1 The evidence bundle

Every execution produces one **content-addressed** bundle: the hypothesis version, the load run summary and script hash, every SLI sample from both sources, the Litmus `ChaosResult`, probe outcomes, the blast-radius computation, the safety-gate decision, all check results, the verdict, the scoring epoch, the cleanup log, and the `git_sha`. The `sha256` of its canonical JSON **is** the run's identity, linked from Slack and the dashboard.

This is what makes three otherwise-impossible things possible: retro-scoring under a new epoch (§21.1), the hermetic replay eval (§21.2), and offline reproduction:

```bash
$ chaosctl replay 9f2c8ab4…          # no cluster, no network, no database
experiment : pod_kill_payment_svc     epoch 7f2a3c…     git 4c19ba2
load       : k6 2.2.0  open model  target 120 rps  achieved 119.4  dropped 0    VALID
hypothesis : v3  "maintains >=99.0% client availability and P99 <=800ms"
  client_availability_holds   worst 0.9942  >= 0.9900   breached 0s      HELD
  client_p99_holds            worst 690ms   <= 800ms    breached 0s      HELD
  no_server_5xx_storm         worst 0.4/s   <= 1.0/s    breached 0s      HELD
  replicas_restored           restored in 18.2s  <= 30s                  HELD
verdict    : HYPOTHESIS_HELD        score 0.94  (weights denom 1.00)
alert      : fired at 71.4s   irreducible 95s   excess -23.6s   (fired EARLY: rule for=30s)
safety     : blast score 34  budget burn 0.014%  gate open  no aborts
signals    : client-server availability gap 0.31pp  (~22 requests never reached a server)
```

### 22.2 Postmortem generation, with a hard boundary

```python
# chaos-framework/src/postmortem/narrator.py
class Narrator:                       # provider-agnostic; model id from config
    """Drafts prose from the bundle. NEVER produces or alters a verdict, score, or number."""

class TemplateNarrator(Narrator):     # DEFAULT: deterministic, offline, no API key
    ...

class LLMNarrator(Narrator):
    """
    Given bundle facts, drafts summary / contributing factors / action items.
    Output is DISCARDED if it references any metric, timestamp, or entity not in the bundle.
    """
```

Four rules, in priority order:

1. **`TemplateNarrator` is the default.** The system works with no API key and no network. A portfolio project that breaks without a paid API is a liability in a live demo.
2. **Provider-agnostic adapter, model id from config.** Never hardcode a vendor at the call site.
3. **Grounding check is not optional.** Every number in the draft must appear in the bundle; a draft that invents one is discarded, and the discard is logged and counted. A hallucinated postmortem is worse than a templated one because it is more convincing.
4. **The LLM never touches a verdict.** Say the boundary before an interviewer probes it: *"The decision layer is deterministic rules — I need to unit-test it, replay it, and hash it. The LLM writes the prose a human then edits, and if it mentions a number that isn't in the evidence bundle, the draft is thrown away. In 2026 everyone claims an AI SRE agent; 'my scoring is deterministic and my writing is AI-assisted' is the more defensible position."*

Litmus now ships an **MCP server** for natural-language interaction with chaos experiments. Worth one line in the README's future-work section as the credible next step for this layer — and worth *not* building, because a natural-language interface to fault injection is a safety surface, not a feature.

### 22.3 Signing

```yaml
- uses: sigstore/cosign-installer@v3
- run: |
    cosign sign --yes ghcr.io/OWNER/chaosproof@${DIGEST}
    cosign attest --yes --predicate bundles/${SHA}.json \
      --type https://chaosproof.io/evidence/v1 ghcr.io/OWNER/chaosproof@${DIGEST}
```

Keyless OIDC — no key material to lose, the CI identity is the signer, and it is one step. The framing: *"A resilience score is a claim about production behaviour. Anyone can put a number on a dashboard. Mine comes with a signed evidence bundle you can replay offline and get byte-identical output — that's the difference between a metric and an assertion."*

---

## 23. Resilience Patterns — of ChaosProof Itself

Rev 1 documented resilience patterns of the **target app** (which are the things being tested) and treated the framework as infallible. The brief asked for the framework's own resilience, and the distinction is a good interview moment on its own: *"the patterns in my target app are the subject; the patterns in my framework are the engineering."*

| Pattern | Where ChaosProof uses it | Failure it prevents |
|---|---|---|
| **Saga / compensating transaction** | Cleanup steps registered before injection, executed on every exit path (§15.6) | An aborted experiment leaving flags disabled, replicas scaled, or a ChaosEngine orphaned |
| **Dead-man's-switch watchdog** | `chaos-cleanup` CronJob every 5 min force-cleans `running` rows past their deadline | Framework pod dies mid-experiment, leaving a fault permanently injected |
| **Independent abort paths** | Litmus `promProbe` with `stopOnFailure` (in-band) **plus** the Python watchdog (out-of-band) (§15.3) | A single abort mechanism that dies with the process it protects |
| **Circuit breaker (transport)** | Prometheus, Alertmanager, K8s API, Slack clients, each with a defined fallback | A slow Prometheus turning every validation into a 30s timeout and the experiment into a false failure |
| **Circuit breaker (policy)** | Chaos breaker on repeated aborts / repeated `INVALID` / sick cluster (§15.5) | Continuing to inject failure into a system that is already failing |
| **Token bucket** | Experiment budget per namespace per day + error-budget gate | Chaos volume scaling up exactly when the cluster is least healthy |
| **Fenced distributed lock** | Redis `SET NX chaos:lock:{namespace}`, TTL 600s, **value = execution id**, ownership re-verified before each mutating call (§C.2) | Two experiments overlapping and making every result unattributable — the failure that would invalidate the entire dataset |
| **Lease-based leader election** | `coordination.k8s.io/Lease` when the framework runs >1 replica | Two schedulers both launching the 3 AM run |
| **Idempotent injection** | `(experiment_type, git_sha, scheduled_at)` idempotency key; Litmus ≥3.29.0 fixed duplicate triggers under concurrent reconciles | Double-injection from a retried CronJob or a reconcile race |
| **Timeout everywhere** | 5s Prometheus queries, 60s Litmus apply, per-stage scenario deadlines, hard experiment ceiling | Unbounded waits recorded as "running" forever |
| **Retry with backoff + full jitter** | Transient Prometheus / K8s / Slack errors only — **never** a re-injection | Thundering-herd retries; a double-injected fault |
| **Bulkhead** | Separate worker pools for experiment execution vs dashboard API vs bisection jobs | A 90-minute bisection starving the daily schedule |
| **Graceful degradation** | No Prometheus → `INVALID` + escalate; no Slack → still runs and records, digest on recovery; no k6 → **refuse to run** | A dependency outage becoming either paralysis or a fabricated result |
| **Fail-closed measurement** | Missing series, insufficient traffic, or dropped iterations → `INVALID`, never `PASS` | The framework reporting success because it measured nothing (§C.1) |
| **Kill switch** | `chaosctl freeze`, namespace label, workload annotation, Helm value | Needing a redeploy to stop chaos during a release |
| **Self-exclusion** | Policy denies any experiment targeting the `chaosproof` namespace (§15.4) | The framework killing the pod holding the experiment's own state |

**The fail-closed rule deserves its own sentence in the interview:** *"The most dangerous failure mode for a chaos framework isn't crashing — it's passing. If my Prometheus query returns an empty series because a label changed, a naive validator sees no failures and reports success. So every missing measurement is `INVALID`, never `PASS`, and `INVALID` doesn't contribute to the score. I would rather my dashboard say 'I couldn't tell' than lie to me."*

---

## 24. Availability & Consistency Patterns

### 24.1 The CAP position, stated deliberately

ChaosProof is **CP for verdicts and AP for observation**:

| Concern | Model | Why |
|---|---|---|
| **Executions, verdicts, epochs, evidence** | **CP — strong consistency.** Single-writer PostgreSQL, transactional, fail-closed on partition | A verdict must never be readable as `held` when the evidence says otherwise. Better to refuse to record than to record a guess |
| **SLI sampling** | **AP.** Best-effort, gap-tolerant, coverage measured | A missed scrape must not abort an experiment — but coverage below 90% makes the run `INVALID` (§16.1), so gaps are counted rather than ignored |
| **Dashboard reads** | **AP.** Cache-aside with stale-while-revalidate | A dashboard that errors during a live demo is worse than one showing 30-second-old data |
| **Cluster state** | Eventually consistent by nature | The K8s API lags. Verification uses windows and thresholds, never instantaneous reads — this is why `tolerance_s` exists in §14.2 |

### 24.2 Availability mechanics

- **Framework:** 2 replicas, but only one *executes* — a `Lease` grants leadership; the follower serves the dashboard API. Read availability is decoupled from execution capacity, and execution must be serial anyway (§11's scaling decision).
- **Load plane:** the k6 `TestRun` is the experiment's dependency, so its readiness is a precondition, not a best-effort. If the TestRun fails to reach steady state within 120s, the experiment is `SKIPPED` before any fault is injected.
- **Prometheus:** the measurement dependency. Run it with adequate retention (30 days minimum, since the error-budget window is 30 days) and treat its loss as measurement loss: `INVALID`, escalate, do not inject.
- **PostgreSQL:** single primary is correct at this scale. `pg_dump` CronJob to S3 plus WAL archiving. **RPO/RTO for the evidence store matters more than for the scheduler** — losing a night's schedule costs a night; losing the evidence store costs the entire resilience history and every retro-scoring capability with it.
- **Valkey/Redis:** treated as **disposable**. Say it plainly: *"if Redis is wiped, ChaosProof loses queue position, the current lock, and cold caches. It does not lose a single execution, verdict, or evidence bundle."*
- **Degraded mode:** if PostgreSQL is unreachable the scheduler **stops injecting** (fail-closed) and posts to Slack. No evidence write means no experiment — the record is a precondition, not a side effect.

### 24.3 Consistency mechanics

- **One transaction per state transition**, with the execution row, its checks, its hypothesis results, and its score written together. A half-written execution is worse than no execution because it silently skews aggregates.
- **Monotonic execution state machine:** `queued → preflight → running → validating → {held | falsified | invalid | aborted | skipped | denied | error}`. Illegal transitions raise, and the raise is recorded.
- **Immutable evidence.** Bundles are content-addressed and write-once; a corrected score is a **new** row under a new epoch referencing the same bundle, never an `UPDATE`. This is what makes retro-scoring auditable rather than revisionist.
- **Optimistic concurrency on K8s writes** via `resourceVersion` preconditions; on conflict, re-read and re-plan rather than retry blindly.
- **Epoch immutability:** a closed epoch is never edited. Changing weights opens a new epoch — which is the mechanism that makes the trend chart honest.

---

## 25. Mitigation Strategies — Failure-Mode Table

| # | Failure mode | Blast radius | Detection | Mitigation |
|---|---|---|---|---|
| 1 | **No traffic during experiment** | Every score meaningless | `achieved_rps < floor` | `INVALID` verdict; load plane is a precondition (§16.1) |
| 2 | **Load generator saturates before the target** | Conclusions unfounded | `dropped_iterations > ceiling` | `INVALID`; raise `maxVUs` or lower target rate |
| 3 | Recovery SLO shorter than metric lag | Recovery times fictional | Alert-rule lint + window audit | 5s scrape, ≥30s windows, k6 as primary clock (§16.3) |
| 4 | Alert SLO arithmetically impossible | Permanent false failure | `evals/alert_rule_lint.py` in CI | Grade *excess* latency; fail the build on impossible rules (§16.5) |
| 5 | Empty PromQL series read as success | Silent false pass | Series-emptiness check | `INVALID`, never `PASS` (§14.3) |
| 6 | `resilience4j-micrometer` missing from classpath | All pattern checks vacuous | Startup assertion on metric presence | Fail readiness if expected series are absent (§C.6) |
| 7 | Two experiments overlap | All results unattributable | Lock acquisition metric; overlap detector | Fenced Redis lock + Lease; Litmus ≥3.29.0 duplicate-trigger fix |
| 8 | Fault causes more harm than predicted | Target namespace, possibly beyond | Continuous `promProbe` + watchdog | Abort, cleanup saga, escalate, trip chaos breaker (§15.3) |
| 9 | Framework dies mid-experiment | Fault left injected | `chaos-cleanup` CronJob | Force-clean past deadline; alert on `orphaned_cleanups_total > 0` |
| 10 | Counterfactual leaves patterns disabled | **Severe** — target has no protection | Flag-snapshot reconciliation each cycle | Restore in saga + CronJob; staging-only by policy (§17.3) |
| 11 | Cascade escapes the blast radius | Cluster-wide | `blast-radius-containment` probe on other namespaces | Abort both stages in reverse order (§18.3) |
| 12 | Error budget spent, chaos continues | Real user impact | Budget gate evaluated pre-flight | `closed` gate at 80% spend; reliability work takes priority (§15.5) |
| 13 | Flaky gate blocks innocent PRs | **Team abandons the gate** | σ and verdict-flip rate on unchanged SHA | Auto-quarantine to advisory + issue filed (§21.3) |
| 14 | Scoring change silently rewrites history | Trend chart is fiction | Epoch hash mismatch | Epoch boundary on chart + retro-score (§21.1) |
| 15 | Bisection converges on noise | Wrong commit blamed | Candidate mean inside flakiness band | `abandoned` outcome; require r reps for separation (§20.2) |
| 16 | Contract weakened without review | Consumers silently exposed | Contract diff in PR | Surface the diff to owners; do not auto-block (§19.3) |
| 17 | Litmus stale probe config | Wrong abort behaviour | Version assertion in CI | Pin Litmus ≥3.28.0 |
| 18 | CPU-hog + HPA scaling paradox | Wrong conclusion about autoscaling | Replica timeline vs throttle ratio | Assert aggregate SLI and throttle ratio, not "spike disappears" (§C.2) |
| 19 | disk-fill hits eviction, not `NodeDiskPressure` | Alert check fails wrongly | Expected-alert mismatch | Assert eviction-and-reschedule; set `ephemeral-storage` limits (§C.2) |
| 20 | Prometheus retention < budget window | Error budget uncomputable | Retention check at startup | ≥30d retention; refuse to gate on an incomputable budget |
| 21 | Public demo endpoint triggers real chaos | External blast radius | Route audit; read-only build flag | Two-tier deploy; no trigger path is internet-reachable (§26.4) |
| 22 | Secret leak (Slack token, GitHub PAT) | External | Least-privilege scopes; secret scanning in CI | Fine-grained PAT (issues + PR on one repo), Slack app scoped to one channel, documented rotation |

---

## 26. Deployment Strategies

### 26.1 Environments

| Environment | Cluster | Chaos posture | Purpose |
|---|---|---|---|
| **local** | `kind` 1.36 + target app + k6 | All experiments advisory | Development; hypothesis authoring |
| **ci** | ephemeral `kind` per PR | 2 gating experiments (pod kill, net latency) + load | The headline gate |
| **staging** | small cloud cluster | All 6 + cascades + **counterfactuals** | Where experiments earn gating status (§21.3); the only place patterns are deliberately disabled |
| **prod (demo)** | small GKE/EKS | Daily 6, low-radius only, budget-gated | The public link and the "45%→92%" history |

Two promotion pipelines, deliberately separate: **software** promotes by `git push → CI → Helm`; an **experiment's authority to block a merge** promotes through flakiness characterisation. Shipping a new framework version never silently grants an experiment gating power — any change to an experiment's hypothesis version resets it to advisory.

### 26.2 RBAC, tightened and justified verb-by-verb

Rev 1's ClusterRole was reasonable; Rev 2 narrows it and — more importantly — can justify each verb, which is the actual interview question:

| Resource | Verbs | Which feature needs it |
|---|---|---|
| `litmuschaos.io/chaosengines, chaosexperiments, chaosresults` | get, list, watch, create, delete | Trigger and monitor experiments |
| `k6.io/testruns` | get, list, create, delete | The load plane (§16.1) |
| `pods`, `pods/log`, `events` | get, list, watch | Observation and evidence capture |
| `deployments`, `deployments/scale` | get, list, watch, **patch** | Replica snapshot/restore in cleanup (§15.6) — the only mutating verb, and it exists for *undo* |
| `horizontalpodautoscalers` | get, list | CPU-spike experiment needs to know if an HPA owns the workload (§C.2) |
| `coordination.k8s.io/leases` | get, create, update | Leader election |
| `configmaps` (own namespace only) | get, list, watch | Hypotheses, policy, contracts |

Deliberately absent, and this is the answer: **no `delete` on deployments or PVCs, no `secrets` read outside its own namespace, no RBAC write, no wildcards, no cluster-admin.** *"The one mutating permission I hold on the target is `deployments/scale`, and I hold it so I can put replica counts back the way I found them. Everything destructive is done by Litmus's own service account inside the experiment, which is scoped per-experiment and disappears with it. My framework's job is to observe and to clean up — the fault injector is the thing with teeth, and it's a CNCF project I didn't write."*

### 26.3 How ChaosProof itself deploys

- **Rolling update, `maxUnavailable: 0`**, readiness gated on PostgreSQL, Redis, Prometheus, and K8s API reachability — because a framework that starts without its measurement plane will produce `INVALID` runs and burn the schedule.
- **`helm upgrade --install --atomic --wait`** so a bad release rolls itself back. Helm 4: remember the **`watch`** RBAC verb for `--wait`.
- **Never deploy during an experiment.** The scheduler holds a deploy lease; the CI deploy job waits for `running` executions to finish or times out. An execution interrupted by a deploy is an `INVALID` run *and* an orphaned fault.
- **Drain semantics:** on `SIGTERM`, stop consuming the queue, finish the in-flight experiment **including cleanup**, release the Lease. `terminationGracePeriodSeconds: 900` — long, because cleanup is not optional and a 60-second grace period would routinely orphan faults.
- **Migrations are expand-contract**, forward-only via Alembic. Additive with release N, destructive with N+1 once no old pods remain.
- **Canary via advisory mode:** deploy the new framework version with all experiments forced advisory for one nightly cycle in staging, and **diff its verdicts against the previous version's on the same bundles** (the replay eval makes this mechanical). That is regression testing for a scoring system.
- **Rollback:** `helm rollback`, plus epoch rollback if the scorer changed. `scorer_version` is on every execution, so any historical number can be attributed to the code that produced it.

### 26.4 Public deployment — two-tier, so the link is safe

1. **Live demo dashboard** (Vercel or cluster ingress): **read-only**, seeded with real recorded executions plus rendered `chaosctl replay` output. No cluster credentials, no trigger path. Manual-trigger controls are absent from the read-only build via a build-time flag, not merely disabled — a control that 404s invites someone to find out why.
2. **The real framework**, only in the demo cluster, behind auth. The public site links a 2-minute video of a live experiment and the CI gate blocking a PR, plus replayable evidence hashes.

**Never expose an endpoint that can inject a fault to the internet.** Say it before you are asked: *"the public demo is read-only by design; the only path that can inject a fault runs inside the cluster."*

### 26.5 Cost

| Item | Provider | Cost |
|---|---|---|
| GKE Autopilot small cluster | Google Cloud | ~$70/mo (₹300 free credit covers ~4 months) |
| OR kind on an EC2 `t3.large` (k6 + Prometheus need headroom) | AWS | ~$60/mo |
| OR local kind | Local | $0 |
| Dashboard | Vercel hobby | $0 |
| Slack app, cosign, GitHub Actions (public repo) | — | $0 |
| **Total** | | **$0–70/month** |

Note the honest upgrade from Rev 1's `t3.medium`: the load plane and 5-second scraping need real headroom, and a chaos experiment that fails because the *node* was starved produces `INVALID` runs, not insight.

---

## 27. README Blueprint with Architecture Diagram

````markdown
# ChaosProof — Chaos Engineering Lab with Automated Recovery Testing

> Don't hope your system is resilient — prove it every day, and refuse to answer when you can't measure.

[![chaos gate](badge)](link) [![replay eval](badge)](link) [![resilience score](badge)](link)

## What it does

ChaosProof injects controlled faults into a Kubernetes microservices application, holds
steady synthetic traffic through the fault, and decides whether the system's behaviour
matched a written hypothesis. Every experiment produces a signed evidence bundle you can
replay offline.

The headline: **chaos runs in CI.** A PR that removes a circuit breaker fails the gate.

## Architecture

```
                       ┌─────────────────── ChaosProof ───────────────────┐
   GitHub PR ──────────►│ CI runner  ──┐                                   │
   CronJob 03:00 ───────►│ Scheduler ───┤                                   │
                        │              ▼                                   │
                        │   ┌──── Pre-flight gate ────┐                    │
                        │   │ steady state · SLI floor │                   │
                        │   │ blast radius · budget    │                   │
                        │   └──────────┬───────────────┘                   │
                        │              │ proceed / skip / deny             │
   ┌─── Load plane ─────┤              ▼                                   │
   │ k6 Operator        │      Experiment runner ──► LitmusChaos CRD ──────┼──► target-app
   │ constant-arrival   │              │                    │              │    (Spring Boot 4
   │ 120 rps            │              │            probes (abort)         │     + Resilience4j)
   └────────┬───────────┘              ▼                    │              │         │
            │              Sampler (5s, dual-source) ◄──────┘              │         │
            │                          │                                  │         ▼
            └──────────────────────────┼──── client SLIs ─────────────────►│    Prometheus
                                       ▼                                   │    (5s scrape)
                              Hypothesis engine                            │         │
                                       │                                   │◄────────┘
                                       ▼                                   │
                      HELD / FALSIFIED / INVALID / ABORTED                 │
                                       │                                   │
                    ┌──────────────────┼──────────────────┐                │
                    ▼                  ▼                  ▼                │
              Score (epoch)      Evidence bundle       Slack               │
                    │             (signed, CAS)                            │
                    ▼                  ▼                                   │
              PostgreSQL 18      chaosctl replay                           │
                    │                                                      │
                    ▼                                                      │
              Next.js dashboard ◄───── Valkey cache                        │
                        └──────────────────────────────────────────────────┘
```

## The six experiments and their hypotheses

| Experiment | Hypothesis (abbreviated) | Verdict |
|---|---|---|
| Pod kill | ≥99.0% client availability, P99 ≤800ms, replicas restored ≤30s | HELD |
| Network latency 500ms | Circuit breaker opens ≤10s, P99 ≤2000ms, fallback served | HELD |
| Network partition | ≥95% availability via fallback, CB opens, alert fires | HELD |
| Disk fill | Pod evicted and rescheduled, no data loss, alert ≤60s | HELD |
| CPU spike | Throttle ratio ≤ ceiling, aggregate SLI holds, HPA reacts | FALSIFIED → fixed |
| Container kill | Recovery ≤15s via readiness probe | HELD |

## What makes this more than a Chaos Monkey clone

1. **Hypothesis-first** — falsifiable claims in YAML, not "did it recover?"
2. **It refuses to lie** — no traffic, missing metrics, or a saturated load generator all
   produce `INVALID`, never `PASS`
3. **Client-side truth** — server-side metrics can't see a request that never reached a server
4. **Safety plane** — blast radius before, abort during, saga cleanup after
5. **Error-budget gated** — chaos does not run when the budget is spent
6. **Counterfactual ROI** — the same fault with the pattern on and off, n=5, medians with IQRs
7. **Contracts generate experiments** — declared tolerances become tests
8. **Flakiness quarantine** — an experiment that can't hold σ<0.05 can't block your merge
9. **Scoring epochs** — the trend chart doesn't silently include scorer edits
10. **Signed, replayable evidence** — `chaosctl replay <sha>` offline, byte-identical

## Quick start
```bash
make kind-up          # kind 1.36 + kube-prometheus-stack + Litmus + k6 Operator
make target-app       # Spring Boot 4 services with Resilience4j
make load             # k6 TestRun, 120 rps open model
make experiment NAME=pod_kill_payment_svc
make dashboard        # http://localhost:3000
```

## Screenshots
1. Resilience gauge + 30-day trend with a labelled epoch boundary
2. Hypothesis verdict with the falsified invariant highlighted
3. Client-vs-server availability overlay, gap shaded
4. Recovery timeline: fault → first failure → alert (with irreducible-lag marker) → restored
5. Counterfactual panel: with/without box plots, IQRs, "inconclusive" state visible
6. CI gate blocking a PR that removed `@CircuitBreaker`
7. Flakiness panel with a quarantined experiment

## Design decisions (ADRs in `docs/adr/`)
- ADR-001 LitmusChaos over Chaos Mesh, Gremlin, xk6-disruptor
- ADR-002 Plain PostgreSQL over TimescaleDB — with the threshold that would change it
- ADR-003 **Open workload model over closed** — why constant-VUs hides the fault
- ADR-004 Sequential over parallel experiments
- ADR-005 Resilience4j over Spring Boot 4 native resilience — observability as the criterion
- ADR-006 Client-side SLIs as primary, server-side as corroboration
- ADR-007 Bounded bisection with visible cost

## What ChaosProof deliberately does NOT do
- Inject faults in production without an error-budget gate and a human-set radius cap
- Run counterfactual (pattern-disabled) experiments outside staging
- Let an LLM produce or alter a verdict
- Report a score when it could not measure — that's what `INVALID` is for
````

---

## 28. Interview Prep — Rev 2 Additions

Rev 1's nine answers stand. These fifteen cover what Rev 2 added — including four questions that would have exposed Rev 1.

**Q10: "How do you know your experiments measure anything?"** *(the question Rev 1 fails)*
> "That's the bug I found auditing my own first version — I had no load generator. Every SLI was successful-requests over total-requests, and with no traffic that's zero over zero, so a pod kill against an idle service looked like a perfect recovery. Now k6 runs in-cluster holding a constant arrival rate through the whole experiment, and every run has a validity gate: if achieved rps is below my floor, or if k6 reports dropped iterations meaning the generator saturated before the system did, the verdict is `INVALID` rather than `PASS`. `INVALID` never contributes to the score. No traffic means no evidence, and no evidence must never look like success."

**Q11: "Why constant arrival rate rather than a fixed number of virtual users?"**
> "Because a closed model hides the fault. With fixed VUs, each user waits for a response before sending the next request — so when the system slows down, offered load automatically drops. A pod kill that doubles latency halves throughput, fewer requests are in flight to fail, and the availability ratio can actually *improve* while users are having a worse time. An open model launches requests on a schedule regardless, so queues build the way real traffic does. The cost is that I can overwhelm the target, so I need VU headroom and I have to watch dropped iterations to tell 'the system is failing' from 'my load generator is failing.'"

**Q12: "How can you measure a 30-second recovery SLO?"** *(the second question Rev 1 fails)*
> "My first version couldn't — it used five-minute rate windows against a thirty-second SLO, so the measurement lagged the thing being measured by an order of magnitude. I fixed it three ways: five-second scrape interval on the target app's ServiceMonitor only, thirty-second rate windows during experiments, and k6's own one-second-granularity time series as the primary recovery clock with Prometheus as corroboration. The client knows when it started succeeding again before Prometheus does."

**Q13: "Isn't server-side availability enough?"**
> "No, and this is my favourite subtlety in the project. A request that fails at connection establishment never reaches a server, so it never increments any server-side counter. During a pod kill — the experiment I lead with — server-side availability actually looks *better* than reality, because the failures are invisible to it. I compute availability from the client's perspective and treat the gap between client and server error rates as its own signal: it's a direct estimate of requests that died before they were ever served."

**Q14: "Your score went 45% to 92%. Did the scoring change during those four weeks?"** *(the third question Rev 1 fails)*
> "It did, once, in week two when I added alert validation as a weighted check — and that's exactly why I introduced scoring epochs. An epoch is a hash of the weights, the experiment set, the SLO version, and the scorer version. The trend chart only connects points within an epoch, boundaries are drawn as labelled vertical lines, and because I persist raw observations rather than just scores I retro-scored the earlier runs under the new weights so the comparison is real. Without that, some of my improvement would have been me editing the scorer, and I'd rather show the seam than have you find it."

**Q15: "What stops a chaos experiment from causing a real outage?"**
> "Three layers at three timescales. Before injection: blast radius computed from current load, plus a steady-state precondition, plus a validity floor, plus an error-budget gate — and I can show the arithmetic that my entire daily programme costs under half a percent of my monthly availability budget. During: continuous Litmus promProbes with `stopOnFailure` watching client availability and containment outside the target namespace, plus an independent Python watchdog, because a safety mechanism owned by the process that can crash isn't one. After: a cleanup saga registered before injection, with a CronJob as the out-of-band path, and cleanup isn't done until steady state is re-established."

**Q16: "Would you run this in production?"**
> "In the shape it's in, with the error-budget gate and a radius cap set by someone accountable — yes, for the low-radius experiments. The gate is the precondition: if eighty percent of the thirty-day budget is spent, my scheduler refuses to inject more failure, because at that point the right engineering decision is to fix reliability rather than keep proving it's broken. What I would not run in production is the counterfactual suite — deliberately disabling a circuit breaker to measure its value belongs in staging, and that's enforced in the policy file, not by my remembering."

**Q17: "Why would your team still have the CI gate enabled in six months?"** *(the fourth question Rev 1 fails)*
> "Because it measures its own flakiness. A flaky gate gets disabled — that's how every abandoned CI check died. So ChaosProof runs each experiment repeatedly against unchanged code, computes the standard deviation of the score and the verdict flip rate, and any experiment that can't hold sigma under 0.05 is automatically demoted to advisory and files an issue against itself. New experiments start advisory and earn the right to block a merge after twenty clean runs. Progressive delivery, applied to my own tooling."

**Q18: "Where's the MLOps in this?"**
> "The scorer is a deterministic model mapping evidence to a number, so I gave it the treatment a model gets: a versioned artefact — the scoring epoch — a labelled dataset of committed evidence bundles, an offline replay eval that gates CI on verdict correctness and a determinism digest, data validation via the SLI-validity gate, shadow deployment as advisory mode, progressive rollout through flakiness characterisation, drift monitoring on score variance, and rollback via epoch revert. Deliberately no machine learning: I need to unit-test, replay, and hash my decisions. The one LLM in the system writes postmortem prose and gets discarded if it cites a number that isn't in the evidence bundle."

**Q19: "You said you'd beat LitmusChaos. But you use it."**
> "Right — and that framing is worth correcting. Litmus is the fault injector, and rebuilding fault injection would be strictly worse than using the CNCF-backed one that Flipkart runs a multi-tenant platform on. What Litmus doesn't do is decide whether the system's behaviour was *acceptable*: it tells you the experiment ran and whether its probes passed. My layer is the verification and safety plane on top — hypothesis evaluation, measurement validity, client-side SLIs, blast-radius and budget gating, counterfactual analysis, contract-generated experiments, and flakiness quarantine. Against Chaos Monkey the comparison is easier: that's a random instance terminator from 2011. The honest claim is narrow and defensible rather than broad and false."

**Q20: "What was the most interesting thing an experiment found?"**
> "A retry-amplification bug, and my contract system found it rather than a human. order-api's contract claimed it tolerated a five percent error rate from payment-service. ChaosProof generated the experiment for that clause, injected exactly five percent packet loss, and order-api's own error rate hit twelve percent — because with three retry attempts, a five percent upstream failure rate becomes about fourteen percent effective load, and the retries amplified the failure they were meant to absorb. The contract was violated by its own retry policy. No schema test would ever find that."

**Q21: "How do you handle a cascading failure?"**
> "One of my experiments is a fault DAG with conditional triggers rather than fixed delays. I kill Redis, and the second fault — a hundred milliseconds of latency on inventory-service — only fires if database P99 actually crosses two hundred milliseconds. The most valuable outcome is when the trigger *doesn't* fire within the timeout, because that's my stale-cache fallback absorbing the cache outage completely, and I can prove that rather than assume it. There's also an observation-only stage after injection stops, because retry storms and queue growth show up after the fault ends — a validate-immediately loop misses them."

**Q22: "Why Resilience4j when Spring Boot 4 has native retry?"**
> "Because native Spring resilience has no circuit breaker, and because this project can only validate patterns that publish their state. Boot 4 gives me `@Retryable` and `@ConcurrencyLimit`; it doesn't give me a circuit breaker with an observable state machine, and it doesn't emit the `resilience4j_*` series my validator reads. I chose the dependency for its observability. An unobservable circuit breaker is unverifiable, and an unverifiable pattern is exactly what this project exists to catch. There's a build trap worth knowing too: `resilience4j-micrometer` isn't a transitive dependency, so without it every pattern check silently has nothing to read while looking like it works."

**Q23: "What's the weakest part of this project?"**
> "Scale. Six experiments a day against three services on one cluster — I've built the verification layer well, but I haven't operated it at a scale where multi-tenancy, chaos-injection HA, or non-Kubernetes workloads matter. Flipkart's KubeCon India talk this June covered exactly those four problems on top of Litmus, and reading it is how I know what I haven't solved. The second weakness is that my counterfactual sample sizes are small — n=5 per arm gives me medians and interquartile ranges, and when they overlap I have to report 'inconclusive' rather than a number, which is honest but less satisfying than the dashboard makes it look."

**Q24: "How would you extend this to a hundred services?"**
> "Three changes, in order. First, experiments become contract-generated rather than hand-written — that's already built, and it's the only way an experiment suite tracks a hundred services. Second, sequential execution stops being viable at that scale, so I'd parallelise across *disjoint* dependency subgraphs while keeping same-service experiments serial, which requires a real dependency graph rather than my three-service topology. Third, blast-radius calculation becomes the bottleneck rather than execution, because 'what else does this touch' is a graph query at that point. What I would not change is the validity gate or the hypothesis engine — those get more important with scale, not less."

---

## 29. Build Order, Competitive Grid, Schedule, Demo Script, Fact-Check

### 29.1 What to actually build

| Tier | Feature | Effort | Interview value | Decision |
|---|---|---|---|---|
| **0** | **§16 Load plane + SLI validity gate** | 2–3 days | **Non-negotiable** — without it nothing else means anything | **Build first, before any other Rev 2 work** |
| 0 | MVP: Litmus + 6 experiments + validators + score + Slack + dashboard + Helm + CI | 4 weeks | Table stakes | Ship |
| 0 | §C.4 scoring fix, §16.3 window fix, §16.5 alert-latency fix | 1 day | Removes three defects a sharp interviewer will find | Ship |
| **1** | **§14 Hypothesis engine** | 3 days | Very high — reframes every experiment | **Ship** |
| **1** | **§15 Safety plane** (blast radius, probes, budget gate, saga) | 4 days | **Highest** — this is what makes it credible beyond CI | **Ship** |
| **1** | **§21 Scoring epochs + replay eval + flakiness quarantine** | 4 days | Very high; also the brief's MLOps requirement | **Ship** |
| 1 | §19 Resilience contracts + generator | 4 days | High — architectural signal, and it found the retry bug | **Ship** |
| 2 | §17 Counterfactual analysis | 4 days | High — the ROI panel | Ship if week 9 is on time |
| 2 | §18 Cascading scenarios | 4 days | High — "real outage" credibility | Ship (needs §15 first) |
| 2 | §22 Evidence bundles + `chaosctl replay` + signing | 3 days | High in enterprise rooms | **Ship the bundle + replay**; signing is one CI step |
| 3 | §20 Bisection | 3 days | Medium-high; depends on §21.3 | Ship if time |
| 3 | §22.2 LLM narrator | 1 day | Medium | Optional, off by default |
| 4 | Multi-region, security chaos, game-day scoring, MCP interface | — | Low now | `docs/future-work/` as ADRs |

**Rule:** if a feature can't appear in the 5-minute demo (§29.5) or in one README screenshot, it belongs in `docs/`. And **§16 before everything** — every day spent on differentiators before the load plane exists is a day spent making unmeasured numbers prettier.

### 29.2 Head-to-head grid (put this in the README)

| Capability | Chaos Monkey | LitmusChaos (alone) | Chaos Mesh | Gremlin | AWS FIS | xk6-disruptor | **ChaosProof** |
|---|---|---|---|---|---|---|---|
| Fault injection | pod/instance kill | ✅✅ 50+ faults | ✅✅ | ✅✅ | ✅ AWS only | ✅ k8s subset | ✅ *(via Litmus)* |
| Scheduling | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| In-experiment probes | ❌ | ✅ | ✅ | ✅ | ✅ | partial | ✅ *(as aborts)* |
| **Load generated as part of the experiment** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ *(is a load tool)* | ✅ §16.1 |
| **Refuses to score unmeasurable runs (`INVALID`)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §16.1 |
| **Client-side SLI as primary truth** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ §16.3 |
| **Falsifiable hypothesis with per-invariant verdict** | ❌ | partial (probes) | partial | partial | ❌ | ❌ | ✅ §14 |
| Alert-firing validation | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §16.5 |
| **Honest alert latency (irreducible lag subtracted)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §16.5 |
| Resilience-pattern activation validated | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Error-budget-gated execution** | ❌ | ❌ | ❌ | partial | ❌ | ❌ | ✅ §15.5 |
| **Blast radius computed pre-flight** | ❌ | ❌ | ❌ | ✅ | partial | ❌ | ✅ §15.2 |
| Chaos in CI blocking merges | ❌ | via CLI | via CLI | ✅ | ❌ | ✅ | ✅ |
| **Gate flakiness measured + auto-quarantine** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §21.3 |
| **Counterfactual pattern ROI** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §17 |
| **Contract-generated experiments** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §19 |
| **Conditional fault DAG (cascades)** | ❌ | workflows (temporal) | ✅ workflows | ✅ scenarios | partial | ❌ | ✅ §18 *(conditional)* |
| **Score comparability across scorer changes** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §21.1 |
| **Signed, offline-replayable evidence** | ❌ | ChaosResult CR | CR | reports | reports | JSON | ✅ §22 |
| Regression bisection | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ §20 |
| Multi-tenancy, HA injection, VM chaos | ❌ | ✅ *(Flipkart's extensions)* | ✅ | ✅✅ | ✅ | ❌ | ❌ |
| Maturity, support, integrations | ✅ (historic) | ✅✅ CNCF | ✅✅ CNCF | ✅✅ | ✅✅ | ✅ | ❌ 12-week project |

Then the honest paragraph, because a table with no losses reads as marketing:

> **Where they win.** LitmusChaos has a CNCF-backed fault catalogue across Kubernetes, Linux, AWS and GCP, monthly releases, and production adopters including Flipkart, Canonical, Intuit, Adidas and Red Hat — ChaosProof *runs on it* and would be strictly worse without it. Chaos Mesh has excellent network chaos. Gremlin is a mature commercial platform with blast-radius controls I have simplified. AWS FIS integrates with things I cannot touch. xk6-disruptor deserves particular respect: it means a thin version of the load-plus-fault idea already exists inside k6, and I should say so before someone else does. **ChaosProof is not a replacement for any of them. It is a demonstration of the verification layer I think chaos engineering is missing: state a hypothesis, prove you can measure it, gate on safety, decide honestly, and refuse to answer when the measurement was invalid.**

**One more competitive note worth carrying into the room:** at KubeCon India in June 2026 the most common question at the Litmus booth was *how to shift chaos left into CI/CD* — which is exactly this project's headline. That is useful validation and a useful risk: it means the idea is in the air, so the differentiation has to be the verification layer, not the CI trick.

### 29.3 Revised schedule — 7 weeks core, 5 weeks differentiators

| Week | Focus | Deliverable that proves it |
|---|---|---|
| **1** | kind 1.36, kube-prometheus-stack (5s scrape on the target), Litmus ≥3.30, Spring Boot 4 + Resilience4j 3 services, **`resilience4j-micrometer` verified present** | `resilience4j_circuitbreaker_state` visible in Prometheus; startup assertion fails if it isn't |
| **2** | **Load plane first (§16)**: k6 Operator, open-model profile, validity gate, dual-source sampler | A pod kill with the load plane off yields `INVALID`, not `PASS` — demo this |
| **3** | Experiments 1–3 with hypotheses (§14); PostgreSQL 18 schema incl. `sli_samples` | `chaosctl run pod_kill` prints per-invariant verdicts |
| **4** | Experiments 4–6 (eviction-based disk fill, throttle-ratio CPU assertions), four validators, corrected scorer (§C.4) | Six experiments, no free half-credit anywhere |
| **5** | **Safety plane (§15)**: blast radius, continuous promProbes, watchdog, budget gate, cleanup saga | An experiment aborts mid-fault and the cluster returns to declared state |
| **6** | Slack Block Kit, Next.js 16 dashboard, Grafana 13 provisioned, epoch-aware trend chart | Dashboard shows an epoch boundary and an `INVALID` run distinctly |
| **7** | **Chaos gate in CI** with load first, Helm 4 chart, deploy, demo video | PR removing `@CircuitBreaker` fails the gate |
| **8** | **§21** scoring epochs, replay eval, flakiness quarantine | A deliberately flaky experiment auto-demotes to advisory |
| **9** | **§19** contracts + generator + PR contract diff | The retry-amplification finding, reproduced |
| **10** | **§17** counterfactual (staging only, n=5, IQRs) | ROI panel with an `inconclusive` case visible |
| **11** | **§18** cascades + **§22** evidence bundles, `chaosctl replay`, cosign | `chaosctl replay <sha>` offline, byte-identical |
| **12** | **§20** bisection if time; ADR writing sprint; polish | 7 ADRs in `docs/adr/`, future-work docs |

### 29.4 The 5-minute demo script (rehearse out loud)

1. **(0:00–0:40) The validity moment.** Dashboard at 92%. "Before I show you a pass, here's a failure of my own tooling." Stop the k6 TestRun, trigger pod-kill. Verdict: **`INVALID` — achieved 0 rps, floor 90.** "My first version scored this as a perfect recovery. No traffic, no evidence."
2. **(0:40–1:40) A real experiment.** Restart load, trigger pod-kill. Live timeline: fault → client errors at 1.2s → server metrics react at 6s (point at the gap) → replicas restored 18.2s → SLI held. Verdict **HYPOTHESIS_HELD**, per-invariant.
3. **(1:40–2:30) The abort.** Trigger the network-partition experiment with the fallback disabled. Client availability crosses 80% → **promProbe trips → fault halted at 14s → cleanup saga → steady state re-established.** "It stopped itself. That's the mechanism that would let me run this in production."
4. **(2:30–3:15) The counterfactual.** Panel: circuit breaker on vs off, n=5, box plots. "~5,000 failed requests prevented per incident, IQRs don't overlap. Next to it, the retry pattern: IQRs overlap, so I report inconclusive."
5. **(3:15–4:15) The CI gate.** PR removing `@CircuitBreaker`. Gate fails: "HYPOTHESIS_FALSIFIED — `circuit_breaker_opens` never activated. Score 0.35." Then the second PR that weakens a contract → **contract diff comment tagging the owners.**
6. **(4:15–5:00) The receipt.** `chaosctl replay 9f2c8ab4` in a terminal with wifi off. Byte-identical output. "Every number on that dashboard has a signed bundle behind it. And the trend line has a vertical bar in week two where my scoring changed — I retro-scored everything before it so the comparison is real."

The order is deliberate: **lead with your own bug.** An engineer who opens by showing how their tooling used to lie to them, and the mechanism that stops it, has established credibility that no green dashboard can buy.

### 29.5 Résumé and README one-liners

> **ChaosProof** — Chaos engineering platform on Kubernetes: LitmusChaos fault injection with hypothesis-driven verification, k6 in-cluster load under an open workload model, client-side SLI measurement, pre-flight blast-radius and error-budget gating, in-experiment abort probes, counterfactual pattern ROI, contract-generated experiments, and a CI gate that blocks PRs which reduce resilience — with automatic flakiness quarantine so the gate stays trusted. *Python 3.14 · Spring Boot 4 · Resilience4j 3 · k6 2.2 · Litmus 3.30 · Prometheus · PostgreSQL 18 · Next.js 16 · Helm 4.*

Single line for the CV: *"Built a chaos engineering platform that runs fault-injection experiments in CI and blocks PRs that reduce resilience; measured with client-side SLIs under constant-arrival-rate load, gated on error budget, with automatic quarantine of flaky gates."*

### 29.6 Fact-Check Appendix — every version claim, with where to re-verify

| Claim | Basis | Re-verify at |
|---|---|---|
| LitmusChaos 3.30.0 released June 2026; monthly cadence; six releases Jan–Jun 2026 | CNCF LitmusChaos Q1–Q2 2026 update (6 Aug 2026) | `github.com/litmuschaos/litmus/releases` — **expect a higher minor than 3.30 by the time you install; pin whatever is current** |
| Litmus 3.29.0 fixed duplicate triggers under concurrent reconciles; added Prometheus metrics | Same source, release notes | Release notes for 3.29.0 |
| Litmus 3.28.0 fixed stale config leak across same-type probes | Same source | Release notes for 3.28.0 |
| Litmus 3.27.0 added Job targeting; removed 1024-char CMD probe limit | Same source | Release notes for 3.27.0 |
| Flipkart won the CNCF End User Case Study Contest India; KubeCon India keynote, Mumbai, 18–19 June 2026; four customisations (hybrid multi-tenancy, DaemonSet HA injection, Script Runner fault, hybrid VM chaos) | Same source | `cncf.io/case-studies/flipkart/` |
| Canonical joined as a Litmus adopter (release 3.28.0 period) | Same source | Litmus adopters list |
| LitmusChaos MCP server exists | Same source (KubeCon India booth notes) | Litmus docs |
| Resilience4j 3 requires Java 21; Resilience4j 2 requires Java 17 | resilience4j GitHub README | `github.com/resilience4j/resilience4j` |
| Spring Boot 4 support added in the 2.4.0 line via `resilience4j-spring-boot4`; artifact omitted from the BOM (issue #2427, 27 Mar 2026) | resilience4j issue #2427 | Check whether a release including the BOM fix has shipped; otherwise pin the module version explicitly |
| `resilience4j-micrometer` is not a transitive dependency | Resilience4j Micrometer docs; Spring Cloud CircuitBreaker metrics docs | `resilience4j.readme.io/docs/micrometer` |
| Spring Boot 4 ships `@Retryable`, `@ConcurrencyLimit`, `@EnableResilientMethods`, and no circuit breaker | Spring Boot 4 resilience write-ups (Jan–Feb 2026) | Spring Boot 4 reference docs, "Resilience" |
| k6 2.0 released 11 May 2026 (OpenTelemetry output, structured JSON, `run-k6-action`, k6 Operator v1.0) | GrafanaCON 2026 coverage | `github.com/grafana/k6/releases` |
| k6 2.2.0 is the current line as of mid-August 2026 | Release trackers, 12–13 Aug 2026 | `github.com/grafana/k6/releases` |
| xk6-disruptor injects Kubernetes faults from k6 | k6 documentation | `grafana.com/docs/k6/latest/` |
| Grafana 13.0.0 released 14 April 2026 | Grafana release record | `github.com/grafana/grafana/releases` |
| PostgreSQL 18.6 released 11 Aug 2026; PG 19 in beta | PostgreSQL release notes | `postgresql.org/support/versioning/` |
| Alertmanager API v1 removed in 0.27; 0.33.1 current (Jul 2026) | Alertmanager release notes | `github.com/prometheus/alertmanager/releases` |
| Helm 3 bug fixes ended 8 July 2026; Helm 4.2.x current | Helm version-skew policy | `helm.sh/docs/topics/version_skew/` |
| Kubernetes 1.36 current, 1.34 in maintenance; cgroup v2 required 1.35+; PSI GA 1.36 | Kubernetes release and feature-gate docs | `kubernetes.io/releases/` |
| Next.js 14 EOL; 15 EOL Oct 2026; 16.3.x current; Node 20 EOL Apr 2026 | Next.js and Node release schedules | `nextjs.org/docs` · `nodejs.org/en/about/previous-releases` |
| Python 3.14 current line | Python release schedule | `devguide.python.org/versions/` |

**Two caveats stated plainly.** First, LitmusChaos ships monthly, so any specific minor version in this document is a floor, not a target — `3.30.0` was June 2026 and the current release when you install will be higher; the pinning *discipline* is the point, not the digits. Second, the Flipkart case-study details come from CNCF's own project update and I have not independently reviewed the talk recording; verify the four customisations before citing them as fact in an interview, and cite them as *"their KubeCon India talk describes..."* rather than as your own analysis.

---

## Final Word — Why This Project Wins SRE Interviews (Rev 2)

Rev 1 demonstrated the three SRE pillars competently: SLO thinking, observability depth, and toil reduction through automation. Rev 2 adds the thing that actually separates a senior signal from a strong junior one — **epistemic discipline about your own measurements.**

The three moments that will be remembered:

**"My first version had no load generator, so every experiment measured nothing."** Finding a defect that invalidates your own headline metric, fixing it structurally, and leading a demo with it is a stronger signal than any green dashboard. Most candidates present work that has never been audited. You present work you audited yourself, and the mechanism you built so it cannot happen again.

**"`INVALID` is a verdict."** A framework that refuses to answer when it cannot measure is doing the hardest thing in observability: distinguishing *no evidence of failure* from *evidence of no failure*. Every tool in the comparison grid conflates them.

**"The gate measures its own flakiness so the team doesn't disable it."** This is operational maturity — knowing that the failure mode of a CI check is social, not technical, and engineering against it.

The DevOps maturity hierarchy an interviewer is scoring against:

- *Level 1 (most freshers):* "I deployed to Kubernetes."
- *Level 2 (good freshers):* "I set up monitoring and alerting."
- *Level 3 (strong):* "I inject faults and verify the system recovers."
- *Level 4 (you):* **"I inject faults, verify recovery against a falsifiable hypothesis, prove my measurement was valid before trusting the result, gate execution on the error budget, and quantify what each resilience pattern is actually worth — and my framework refuses to score a run it couldn't measure."**

**The sentence that wins:** *"I run chaos in CI, so a PR that removes a circuit breaker can't merge. But the part I'd want to tell you about is the bug I found in my own first version: I had no load generator, so every SLI was zero over zero and every experiment passed. Now the framework refuses to score any run where it couldn't measure — and that's the difference between a resilience score and a resilience claim."*

---

*Document prepared as a complete implementation guide for ChaosProof — Chaos Engineering Lab with Automated Recovery Testing. Revision 2, August 2026. Supersedes Rev 1 and incorporates the innovative-features document.*
