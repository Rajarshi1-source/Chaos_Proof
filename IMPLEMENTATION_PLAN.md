# ChaosProof — Implementation Plan

**Derived from** `ChaosProof_Implementation_Plan_Rev2.md` (3,462 lines) + the eight `chaosproof-*` skill files, **reconciled against a verified audit of the working tree** on 23 August 2026.

Rev 2 is the *design*. This document is the *build order*: what is already on disk, what diverges from the design, which ruling applies to each divergence, and the dependency-ordered phases with a hard gate on each.

Section references in the form `§16.1` point into `ChaosProof_Implementation_Plan_Rev2.md`. Skill references point at the root `chaosproof-*.SKILL.md` files.

---

## Part 0 — Verified audit of the working tree

Everything below was read from disk, not assumed. Git tree is clean; all 35 tracked files are committed at `8dd5ef1`.

### 0.1 What exists

| Area | State |
|---|---|
| `order-api/`, `payment-service/`, `inventory-service/` | Spring Initializr skeletons — `@SpringBootApplication` class + empty `contextLoads()` test only. No controllers, services, resilience config, or Dockerfiles. |
| Spring Boot | **4.1.1** on all three, Java 21, Maven, jar packaging |
| Group / artifact | `com.chaosproof` / `order-api`, `payment-service`, `inventory-service` — **matches the plan exactly**. Prometheus `application=` labels, Helm chart names, K8s Service names and contract `service:` fields all key off these. |
| `application.yaml` | Present on all three (YAML, not `.properties`) containing only `spring.application.name` — set correctly |
| Common dependencies | `spring-boot-starter-actuator`, `spring-boot-starter-webmvc`, `micrometer-registry-prometheus` (runtime), `lombok` (optional, with annotation-processor paths) |
| `inventory-service` extras | `spring-boot-starter-data-jpa`, `postgresql` (runtime) — **no MySQL**, correct |
| Skill sources | Eight `chaosproof-*.SKILL.md` at repo root + two `references-*.md`, all byte-identical to the packaged bundles |
| Skill bundles | Eight `.claude/skills/*.skill` — **ZIP archives, not extracted** |
| Design doc | `ChaosProof_Implementation_Plan_Rev2.md` — single copy. *(The byte-identical duplicate feature doc flagged earlier is already gone.)* |

### 0.2 Toolchain — installed and matching the pins

| Tool | Installed | Plan pin | |
|---|---|---|---|
| Java | 21.0.11 LTS | 21 LTS | ✅ |
| Python | 3.14.6 | 3.14 | ✅ |
| Node.js | 24.12.0 | 24 LTS | ✅ |
| Docker | 29.7.2 | — | ✅ |
| kubectl | 1.36.1 | 1.36 demo / 1.35 floor | ✅ |
| Helm | 4.2.4 | 4.2.x | ✅ |
| kind | 0.32.0 | `kindest/node:v1.36.x` | ✅ |
| k6 CLI | **not installed** | 2.2.x | ⚠️ optional |

k6 CLI is only needed to lint and dry-run load scripts locally — the load plane itself runs in-cluster as a k6 Operator `TestRun` CR (§16.1), which is deliberate: the network path under test must be the real one. Install it for authoring convenience, not as a dependency.

### 0.3 What does not exist at all

Every non-Java component of the project is unwritten:

`chaos-framework/` (the entire Python validation framework) · `dashboard/` (Next.js) · `litmus-experiments/` · `charts/` · `monitoring/` · `.github/workflows/` · `docker-compose.yml` · `Makefile` · `README.md` · `slos/definitions.yaml` · `policy/chaos_safety.yaml` · `scenarios/` · `evals/` · `profiles/` (k6 scripts) · `migrations/` · `docs/adr/` · `EXPERIMENTS.md` · Dockerfiles for the three services.

### 0.4 Divergences found, with rulings

Ten divergences between the design, the skills, and what is on disk. Each needs a decision before Phase 1.

---

**D-A — The eight skills are packaged but not installed.** `.claude/skills/*.skill` are ZIP archives (verified: `PK` header, contents `<name>/SKILL.md` plus `references/`). Claude Code loads skills from `.claude/skills/<name>/SKILL.md` directories. None of the eight appear in the active skill list, so **none of the project's rules are currently being applied by default.**

> **Ruling:** extract in place. This is Phase 0, task 1 — every subsequent phase assumes these rules are live.

---

**D-B — Doubled directory nesting.** Services sit at `order-api/order-api/`, `payment-service/payment-service/`, `inventory-service/inventory-service/` — an artifact of extracting each Initializr zip into a directory of the same name. §5.1 expects `target-app/<service>/`.

> **Ruling:** flatten to `target-app/<service>/` via `git mv`, matching §5.1. Docker build contexts, Helm chart paths, the compose file in §9.1, and `target-app/order-api/resilience-contract.yaml` in §19.1 all assume this layout.

---

**D-C — Package names carry underscores.** Generated packages are `com.chaosproof.order_api`, `com.chaosproof.payment_service`, `com.chaosproof.inventory_service`. §5.1's tree shows `com/chaosproof/orderapi/`. Current Initializr sanitises the artifact's hyphen to an underscore rather than removing it.

> **Ruling: keep the generated form.** Nothing outside the JVM references the package path — Prometheus's `application=` label comes from `spring.application.name`, not the package. Renaming touches every file and import for zero external benefit. §5.1's tree is a sketch; the artifact names, which *are* load-bearing, already match.

---

**D-D — Three dependencies are missing from all three poms.** `resilience4j-spring-boot4`, `resilience4j-micrometer`, and AspectJ weaving support are absent. *(Build-verified correction: `spring-boot-starter-aop` was removed in Boot 4 GA — last published at `4.0.0-M2`; use `org.aspectj:aspectjweaver`, whose version the 4.1.x parent manages.)* This is precisely the trap `chaosproof-spring-target-app` names: none of the three is selectable on Initializr, and `resilience4j-micrometer` is **not transitive**.

> **Ruling:** add all three by hand to each `pom.xml` in Phase 0, using the XML block in the skill. Consequence if skipped: no `resilience4j_*` series exist, every pattern check reports `applicable=false`, and the framework validates nothing while appearing to work. Phase 1's gate exists to prove this cannot happen silently.
>
> Verify whether the Resilience4j BOM now includes `resilience4j-spring-boot4` (issue #2427, open as of March 2026). If not, pin `${resilience4j.version}` explicitly on both modules.

---

**D-E — Spring Boot version: plan says 4.0.x, skill says 4.1.x, disk has 4.1.1.**

> **Ruling: 4.1.1 stands.** `chaosproof-spring-target-app` (updated 22–23 Aug 2026) supersedes §B's row and floats the patch deliberately — the *line* is what matters, the digit is not load-bearing and ships roughly monthly. §B's `4.0.x` row is stale; treat 4.0.x only as the portfolio-consistency fallback (`4.0.7`, never `4.0.6` — seven CVEs).

---

**D-F — `spring-boot-starter-webmvc`, not `spring-boot-starter-web`.** The generated poms use `spring-boot-starter-webmvc`, and `spring-boot-starter-webmvc-test` / `spring-boot-starter-actuator-test` in place of `spring-boot-starter-test`. This is the Boot 4 starter split.

> **Ruling:** use the generated names. Any snippet elsewhere referencing `spring-boot-starter-web` or `spring-boot-starter-test` predates Boot 4 and must not be pasted in.

---

**D-G — `application.yaml` is nearly empty.** It contains `spring.application.name` and nothing else. Missing: actuator endpoint exposure, `management.metrics.tags.application`, per-service `server.port`, and every `resilience4j.*` instance block.

> **Ruling:** author the full config in Phase 0/1. **`management.metrics.tags.application` is the highest-stakes line** — every ChaosProof query filters on `application=`, and an unset tag makes every pattern query return empty, which a naive validator reads as a pass. It is the exact failure mode §14.3 and the hypothesis engine's empty-series rule exist to catch.

---

**D-H — Initializr placeholder blocks.** All three poms carry empty `<name/>`, `<description/>`, `<url/>` and stub `<licenses><license/></licenses>`, `<developers><developer/></developers>`, `<scm>` elements.

> **Ruling:** fill `<name>` and `<description>` from the metadata table in the skill; delete the empty `<url/>`, `<licenses>`, `<developers>` and `<scm>` stubs. Cosmetic, but they emit Maven warnings on every build and a warning-free build is worth more than the thirty seconds.

---

**D-I — `db_query_seconds` does not exist and cannot come from defaults.** §18.2's cascade stage `db_pressure` triggers on `histogram_quantile(0.99, sum(rate(db_query_seconds_bucket[30s])) by (le)) > 0.200`. Spring's default JDBC and Hikari metrics do not emit a histogram under that name.

> **Ruling:** create it explicitly in `inventory-service` with a `@Timed`-annotated repository wrapper (Phase 1). `@Timed(value = "db_query", …)` produces `db_query_seconds_*`. Requires AspectJ weaving (`org.aspectj:aspectjweaver`, D-D) and a registered `TimedAspect` bean — verify it is present at startup rather than assuming auto-configuration, and remember `@Timed` is proxy-based, so the annotated method must be called from *outside* its own bean.

---

**D-J — The alert-latency SLO is arithmetically impossible as written.** §3's `slos/definitions.yaml` sets `alert_latency.target_seconds: 60`. With the project's own pins the floor is:

```
irreducible = scrape_interval + evaluation_interval + rule `for:`
            = 5s             + 30s                 + 60s          = 95s
```

A correctly configured alerting stack fails this SLO on every single run.

> **Ruling:** keep the 60s SLO and set **`for: 20s`** on the chaos alert rules → floor `20 + 30 + 5 = 55s ≤ 60s`. A short `for:` is right here anyway: fault durations are 30–60s, so a one-minute `for:` would routinely miss the fault entirely.
>
> *Alternative if a longer `for:` is wanted for noise suppression:* keep `for: 1m` and raise the SLO to 120s. Either way `evals/alert_rule_lint.py` (§16.5) enforces the arithmetic in CI, and §16.5's `excess_latency` is what gets graded — not raw detection latency.

---

## Part 1 — The shared contract

Three skills encode rules that only make sense together, and a session that loads one without the others can apply half a rule. Stated once, here, as the authority:

### The verdict vocabulary

| Verdict | Meaning | Scoreable | Trend chart | Owner |
|---|---|---|---|---|
| `HYPOTHESIS_HELD` | Every invariant held within tolerance | ✅ | included | hypothesis-engine |
| `HYPOTHESIS_FALSIFIED` | ≥1 invariant breached beyond tolerance | ✅ | included | hypothesis-engine |
| `INVALID` | **The experiment could not measure what it claimed to** | ❌ **no score at all — not zero** | excluded | measurement-integrity defines the triggers |
| `SKIPPED` | Pre-flight refused: no steady state, or below the SLI floor | ❌ | excluded | safety-plane |
| `DENIED` | Policy refused: blast radius, budget gate, protected target | ❌ | excluded | safety-plane |
| `ABORTED` | Halted mid-fault by a probe or the watchdog | ❌ | excluded | safety-plane |
| `ERROR` | Framework fault | ❌ | excluded | — |

Three rules that span skills and must be applied together:

1. **`INVALID` never scores.** Not as zero — a zero drags the aggregate as if the system failed, when nothing was measured. (hypothesis-engine)
2. **What produces `INVALID`:** achieved rps below floor · `dropped_iterations` above ceiling · sample coverage below 90% · any empty or missing series · measurement dependency unavailable. (measurement-integrity)
3. **Quarantining an experiment opens a new scoring epoch,** because only gating experiments count in the epoch's `experiment_set` and the score now means something different. (mlops-quality)

### Threshold constants — one module, and changing any of them opens an epoch

```python
# chaos-framework/src/constants.py
MIN_SAMPLE_COVERAGE      = 0.90
DROPPED_CEILING          = 10
STEADY_STATE_WINDOW_S    = 120
RECOVERY_HOLD_S          = 30
THROTTLE_RATIO_CEILING   = 0.05
PSI_FULL_CEILING         = 0.05
RESTART_TOLERANCE        = 0
TARGET_SCRAPE_INTERVAL_S = 5
SLI_WINDOW_S             = 30
SAMPLE_INTERVAL_S        = 5
WEIGHTS = {"slo_recovery": 0.35, "alert_validation": 0.25,
           "resilience_pattern": 0.25, "recovery_completeness": 0.15}
PASS_THRESHOLD           = 0.80
FAIL_THRESHOLD           = 0.50
FLAKINESS_WINDOW         = 20
MAX_SCORE_STDDEV         = 0.05
MAX_VERDICT_FLIP_RATE    = 0.05
BUDGET_WARN_PCT          = 50.0
BUDGET_FREEZE_PCT        = 80.0
MAX_BUDGET_BURN_PCT      = 2.0
```

---

## Part 2 — Phases

Ordered by dependency, not by convenience. §29.1's rule governs: **§16 before everything** — every day spent on differentiators before the load plane exists is a day spent making unmeasured numbers prettier.

Each phase ends with a **gate**: a demonstrable condition, not a checklist. If the gate does not pass, the next phase does not start.

---

### Phase 0 — Repo foundation

**~2 days. Not in Rev 2, which assumed greenfield.**

1. **Extract the eight skill bundles** so the project's rules are actually live:
   ```bash
   cd .claude/skills && for f in *.skill; do unzip -oq "$f"; done && rm -f *.skill
   ```
   Produces `.claude/skills/<name>/SKILL.md` ×8 plus two `references/` files. Keep the root `.SKILL.md` copies or delete them — they are byte-identical; keeping them costs nothing and makes the rules greppable outside a Claude session.

2. **Flatten the service layout** (D-B): `git mv order-api/order-api target-app/order-api`, likewise for the other two; remove the now-empty outer directories.

3. **Fix the three poms** (D-D, D-H): add `resilience4j-spring-boot4` + `resilience4j-micrometer` (pinned 2.4.0 — the BOM omits `-spring-boot4`, verified) and `org.aspectj:aspectjweaver` (parent-managed; Boot 4 dropped the AOP starter); fill `<name>`/`<description>`; delete the empty `<url/>`, `<licenses>`, `<developers>`, `<scm>` stubs. Verify the BOM before trusting it to resolve `resilience4j-spring-boot4`.

4. **Scaffold the directory skeleton** from §5.1 with `.gitkeep` files: `chaos-framework/src/{orchestrator,experiments,validators,hypothesis,safety,scoring,quality,contracts,counterfactual,bisect,postmortem,integrations,db,cache}`, `dashboard/`, `litmus-experiments/`, `charts/{chaosproof,target-app,litmus}`, `monitoring/grafana/`, `.github/workflows/`, `slos/`, `policy/`, `scenarios/`, `profiles/`, `evals/`, `migrations/`, `docs/adr/`.

5. **Makefile** with the §27 targets stubbed: `kind-up`, `target-app`, `load`, `experiment`, `dashboard`.

> **GATE 0** — `mvn -q package` succeeds in all three services with **zero warnings**; `ls .claude/skills/*/SKILL.md` returns eight paths.

---

### Phase 1 — Week 1: the target app publishes its state

**Goal: `resilience4j_circuitbreaker_state` is visible in Prometheus, and the app refuses to start if it is not.**

- **Resilience4j configuration** in each `application.yml`, with instance names exactly as the experiments assert (`chaosproof-spring-target-app`). A renamed instance makes the query return empty, which reads as a pass:
  - `order-api` — circuitbreaker `paymentService`, retry `inventoryService`, 3s global timeout
  - `payment-service` — bulkhead `paymentProcessing`, ratelimiter (100 rps), queue fallback
  - `inventory-service` — retry to DB, circuitbreaker to cache, stale-cache fallback
- **`management.metrics.tags.application: ${spring.application.name}`** on all three (D-G).
- **`management.endpoints.web.exposure.include: health,prometheus,metrics`**.
- **Ports** 8080 / 8081 / 8082 to match §9.1's compose.
- **Controllers, services, and the inter-service call graph** — `order-api` → `payment-service` and `order-api` → `inventory-service`, with the `/api/orders/checkout` endpoint the k6 profile drives.
- **`ResilienceMetricsAssertion implements ApplicationRunner`** — fails startup when the four required meters are absent, wired into readiness so a rolling update cannot replace a working pod with a blind one. Note that some meters only register after first exercise: pre-register instances at startup or run a synthetic warm-up call before asserting.
- **Retry amplification, preserved deliberately.** `maxAttempts: 3` against a 5% upstream error rate yields ~14% effective — this is the project's best single finding (§19.2, Q20). Keep the ability to reproduce it: either cap attempts at 2, add a retry budget, **or declare the amplified rate in the contract**. Do not silently "fix" it.
- **`@Timed` db_query wrapper** in `inventory-service` (D-I).
- **Chaos fixtures**: `crashy-api` (`CRASH_AFTER=30s`), `hungry-worker`, `cpu-burner`, `log-spammer`. Fixture names are the `chaos_fixture` value on each experiment — keep them in sync.
- **`ephemeral-storage` limits** on every pod targeted by disk-fill — without limits the experiment has nothing to exceed and silently does nothing.
- **Dockerfiles** ×3, non-root.
- **kind 1.36 cluster**, `kube-prometheus-stack` chart-pinned, **`scrapeInterval: 5s` on the target app's ServiceMonitor only** (`scrapeTimeout` strictly less), **≥30d retention** (the error-budget window).
- **LitmusChaos ≥ 3.30, exact tag pinned.** Expect a higher minor by install time; pin whatever is current.
- **Alert rules** as a `PrometheusRule` CR, five rules, **`for: 20s`** per D-J.

> **GATE 1** — `resilience4j_circuitbreaker_state{application="order-api",name="paymentService"}` returns a value in Prometheus. Then prove the guard works: remove `resilience4j-micrometer` from `order-api`'s pom, rebuild, and confirm readiness **fails** rather than the pod coming up blind. Restore.

---

### Phase 2 — Week 2: the load plane, before anything else

**This is §29.1 Tier 0 and the whole reason Rev 2 exists. Nothing downstream means anything until this gate passes.**

- **k6 Operator v1.0** installed; load runs in-cluster as a `TestRun` CR — never from a laptop or a GitHub runner.
- **`profiles/steady_120rps.js`** using `executor: 'constant-arrival-rate'` — the **open** model. Never `constant-vus`: a closed model couples offered load to system health, so a pod kill that doubles latency halves throughput and the availability ratio can *improve* while users suffer.
- **`preAllocatedVUs: 200`, `maxVUs: 800`** — headroom so k6 never becomes the bottleneck; `thresholds` including `'dropped_iterations': ['count<10']`.
- **k6 remote-writes into the same Prometheus**, because the safety-plane probes query k6's series (Phase 5 depends on this).
- **The validity gate** — `is_valid(min_rps_floor)` checking achieved rps, `dropped_iterations`, and sample coverage, each with a distinct `invalidity_reason`.
- **The dual-source sampler** with **fixed-deadline scheduling** (`next_tick += interval`), recording *actual* sample timestamps. Rev 1's `await asyncio.sleep(5)` after sequential awaits drifted by the query latency and skewed every timeline.
- **`sli_samples` table**, partitioned monthly — the retro-scoring substrate, not logging.
- **`chaosproof_client_server_availability_gap`** exported: `client_error_rate − server_error_rate` ≈ requests that never reached a server.
- **The corrected PromQL library** from `references-promql-and-sli.md` — `[30s]` windows at 5s scrape, throttle **ratio** not raw counters, `increase(container_oom_events_total[2m])` not the flapping gauge, `max_over_time` for circuit-breaker transitions.

> **GATE 2** — stop the k6 `TestRun`, trigger pod-kill, and confirm the verdict is **`INVALID` — achieved 0 rps, floor 90**, not `PASS`. This is also demo beat 1 (§29.4). Record it now while it is easy.

---

### Phase 3 — Week 3: hypothesis engine + experiments 1–3 + schema

- **Hypothesis YAML schema** (§14.2) with the two invariant kinds kept distinct: `tolerance_s` (hold-throughout, describes degradation budget) vs `recover_within_s` (recovery, expected to breach). Rev 1 scored both with one check, which is why a service that never degraded and one that degraded and recovered scored identically.
- **`evaluate()`** with the **empty-series rule**: a missing series returns `invalid`, never a pass. This is the single most common way a home-grown chaos framework lies to you.
- **Per-invariant persistence** to `hypothesis_results` — `worst_value`, `threshold`, `breached_for_s`, evidence window. "The experiment failed" is not actionable; "`client_p99_holds` breached 800ms for 34s" is.
- **PostgreSQL 18.6 schema**: §6.2 core tables + `migrations/002_rev2.sql`'s eight new tables, with `uuidv7()` keys and `weights_denominator` on `experiment_executions`.
- **Experiments 1–3**: pod kill, network latency, network partition — each with hypothesis, `min_rps_floor`, and `abort_conditions`.
- **`chaosctl run <experiment>`** printing per-invariant verdicts.

> **GATE 3** — `chaosctl run pod_kill_payment_svc` prints a per-invariant verdict table with `worst_value` and `breached_for_s` per invariant.

---

### Phase 4 — Week 4: experiments 4–6 + validators + the corrected scorer

**Three of Rev 1's six experiments asserted the wrong thing. A wrong assertion produces a confident wrong verdict.**

| # | Experiment | Assert this | Not this |
|---|---|---|---|
| 4 | Disk fill | **Pod evicted and rescheduled**, no data loss, alert ≤60s | ~~`NodeDiskPressure` fires~~ |
| 5 | CPU spike | **Throttle ratio rises, aggregate SLI holds, HPA reacts** | ~~"the spike goes away"~~ |
| 6 | Container kill | Recovery ≤15s via readiness probe, **CRI/containerd** | ~~"Docker-level"~~ |

- **Disk fill** consumes the *container's* ephemeral storage and needs `ephemeral-storage` limits (Phase 1). Assert `increase(kube_pod_status_reason{reason="Evicted"}[5m])`. Filling the node's filesystem to assert `NodeDiskPressure` is a legitimate but *different* experiment with a different blast radius — do not conflate.
- **CPU spike — the scaling paradox.** `pod-cpu-hog` burns CPU *inside* the target container, so HPA adds healthy replicas while the hogged pod stays hogged; with CPU limits set the hog is throttled and utilisation may barely move. Read `horizontalpodautoscalers` in pre-flight so the hypothesis knows whether an HPA owns the workload.
- **Container kill**: `CONTAINER_RUNTIME: containerd`, `SOCKET_PATH: /run/containerd/containerd.sock`. Say "container-runtime level" — "Docker-level" dates the work by four years.
- **The four validators** as sub-evidence, each declaring `applicable`.
- **The corrected scorer** — non-applicable checks are **excluded and the remaining weights renormalised**, never given 0.5 partial credit. Rev 1's disk-fill 0.625 is only reachable as `0.35 + 0 + (0.5 × 0.25) + 0.15`, which systematically rewards experiments having no resilience pattern. Corrected: **0.588**; daily aggregate 87.1% → **86.5%**. Persist `weights_denominator` so any stored score can be re-derived rather than merely trusted.

> **GATE 4** — all six experiments run; re-score the §4 worked example and confirm disk fill lands at **0.588**, not 0.625. No free half-credit anywhere.

---

### Phase 5 — Week 5: the safety plane

**Build this before the cascade simulator (Phase 11). A fault DAG without a working abort path is the one feature here that could genuinely take down a cluster.**

- **Pre-flight** in order — steady state, SLI floor, blast radius, budget gate, CEL policy. `SKIPPED` and `DENIED` are **recorded outcomes with Slack messages and dashboard treatment**, not silent no-ops: "the framework correctly refused to run" proves the guardrails are load-bearing.
- **Blast radius** computed from the *current* load rate, not a guess. Keep the deliberately simple `score()` arithmetic — a reviewer must be able to evaluate it mentally.
- **The budget arithmetic**, derivable on a whiteboard: 99.5% over 30d = 12,960 error-seconds; a p95 experiment at 4% error for 45s costs 1.8 error-seconds = 0.014%; daily × 30 = **0.42% of the monthly budget**.
- **Two independent abort paths.** Litmus `promProbe` with `mode: Continuous` + `stopOnFailure: true` (in-band, fast, dies with the runner) **plus** an out-of-band Python watchdog reading the same `abort_conditions` from the hypothesis YAML, so the two cannot drift. A safety mechanism owned by the process that can crash is not a safety mechanism.
  - `mode: EOT` is a validator, not a guard.
  - **Pin Litmus ≥ 3.28.0** — the stale-config leak across multiple probes of the same type was fixed there, and both probes are `promProbe`.
  - Probe queries read **k6's** series (Phase 2's remote-write). A guard reading server-side metrics cannot see failures that never reached a server.
- **`policy/chaos_safety.yaml`** evaluated with `cel-python`. First DENY wins, then REQUIRE_OVERRIDE, else ALLOW. Includes `deny-chaosproof-self` — **ChaosProof refuses to inject chaos into ChaosProof**, in the policy file rather than in code.
- **The error-budget gate**, three states, counting **all** budget burn including real incidents — the question is how much reliability allowance is left, not how much chaos consumed.
- **The chaos breaker**: 3 aborts in 24h · 2 consecutive `INVALID` for one experiment (the measurement plane is broken) · K8s API error rate >20% · manual `chaosctl freeze`. Recovery `open → half_open` after 6h, one low-radius experiment decides.
- **Cleanup as a saga**, registered *before* injection, run on every exit path. `restore_feature_flags` is the highest-stakes step. `verify_steady_state` is last — cleanup is not done until steady state is back, and if it cannot be, escalate.
- **`chaos-cleanup` CronJob** every 5 minutes as the out-of-band path; alert on `chaosproof_orphaned_cleanups_total > 0`.
- **Fenced Redis lock** — `SET NX chaos:lock:{namespace}` with the **execution id as the value**, ownership re-verified before every mutating call. An unfenced lock lets a stalled runner resume and act while a successor holds it, making every result unattributable.

> **GATE 5** — trigger network-partition with the fallback disabled; client availability crosses 80%; the probe trips, the fault halts mid-experiment, the cleanup saga runs, and steady state is re-established. Verdict `ABORTED`. This is demo beat 3.

---

### Phase 6 — Week 6: Slack, dashboard, Grafana

- **Slack Block Kit** reports with per-invariant results and a Grafana deep link scoped to the experiment's time range.
- **Next.js 16.3 App Router**, Node 24, dark-first, shadcn/ui copied into `components/ui`.
- **The `Verdict` discriminated union** drives `VerdictBadge`, so a new verdict is a compile error rather than a silently grey badge.
- **`invalid` gets its own colour token** — not red, not green. Styling it red implies the *system* failed when nothing was measured. `skipped` / `denied` / `aborted` are never styled as failures; a red "denied" badge contradicts the project's entire thesis.
- **The epoch-aware trend chart** — segments break at boundaries, boundaries render as labelled vertical lines with `change_reason`, retro-scored points render distinctly, and the cache is **keyed by epoch** or the comparability bug reappears at the presentation layer.
- **The validity strip** — achieved rps vs floor per run, red where `INVALID`. First thing to show in a demo.
- **Client-vs-server availability overlay** with the gap shaded and explicitly labelled.
- **Recovery timeline** with the **irreducible-latency marker**, so a 71s alert on a 95s floor reads as fired-as-fast-as-physics-allowed rather than slow.
- **Grafana 13.0.x**, dashboards provisioned as JSON in-repo, including Litmus's own metrics (3.29.0+) so a silent platform failure is distinguishable from "everything passed".
- All four states — loading, empty, error, stale — for every data view.

> **GATE 6** — the dashboard renders an epoch boundary as a labelled break in the trend line, and an `INVALID` run in a colour that is neither the pass nor the fail colour.

---

### Phase 7 — Week 7: the CI chaos gate

**The headline.**

- `.github/workflows/chaos-gate.yml` with the §21.5 job shape: `unit` → `replay-eval` → `contract-gate` → `chaos-gate` → `build-sign-deploy`.
- **`kubectl apply -f k6/testrun-ci.yaml` runs before `ci_runner`.** This is the single most important line in the file — a chaos gate with no load plane reproduces defect D1 *inside the pipeline*, where a green badge hides it.
- Chart versions pinned; `kindest/node:v1.36.0`; cluster dump on failure so a flaky chaos test is debuggable.
- **Helm 4 charts** — remember `--wait` needs the **`watch`** RBAC verb, and server-side apply is the default for new installs.
- **RBAC**, justified verb by verb. The only mutating verb on the target is `deployments/scale`, held so cleanup can restore replica counts. Deliberately absent: no `delete` on deployments or PVCs, no `secrets` outside its own namespace, no RBAC write, no wildcards, no cluster-admin.
- **`--cov-fail-under=80` on the scorer specifically** — it is the one component whose bugs are invisible, because a wrong number looks exactly like a right number.

> **GATE 7** — open a PR removing `@CircuitBreaker` from `order-api`'s payment call. The gate fails with `HYPOTHESIS_FALSIFIED — circuit_breaker_opens never activated`. Restore it; the gate passes. This is demo beat 5.

---

### Phase 8 — Week 8: the MLOps wrapper

- **Scoring epochs** — `sha256(weights ‖ gating experiment set ‖ SLO version ‖ scorer version)`, stamped on every execution. Epochs are **immutable**; a corrected score is a new row under a new epoch referencing the same bundle, never an `UPDATE`.
- **Retro-scoring** the last N runs from stored `sli_samples`. This is the entire reason raw samples are persisted rather than just scores — and it is what makes the 45%→92% story survivable under questioning.
- **`evals/replay_eval.py`** — hermetic, no cluster or network, runs in seconds on every PR. Corpus ≥20 bundles including the regression cases for every fixed defect: `no_load_zero_traffic` → `invalid` · `pattern_not_applicable` → 0.588 · `empty_promql_series` → `invalid` · `alert_for_exceeds_slo` · `dropped_iterations_high` · `abort_mid_experiment` · `epoch_mismatch` refused.
- **The determinism digest**, frozen per `SCORER_VERSION`. When a deliberate change moves the digests, the reviewer updates the frozen set *in the same PR* — which makes every scoring-affecting change visible in review. That is the point, not an inconvenience.
- **`evals/policy_eval.py`** (every safety rule has a must-allow and a must-deny case) and **`evals/alert_rule_lint.py`** (D-J's arithmetic).
- **Flakiness quarantine** — σ and verdict-flip rate on unchanged `git_sha`, window 20. A new experiment is **advisory-only until characterised** and earns gating status after 20 clean runs. A flaky one auto-demotes with a Slack notice naming σ and a GitHub issue filed against *the experiment*. Quarantine is not deletion. Bumping `hypothesis.version` resets to advisory.

> **GATE 8** — deliberately destabilise one experiment; confirm it auto-demotes to advisory, files an issue, and that the demotion **opens a new epoch**.

---

### Phase 9 — Week 9: resilience contracts

- `target-app/<service>/resilience-contract.yaml` with `provides`, `tolerates`, `does_not_inflict`.
- **The generator emits one experiment per `tolerates` clause** — the suite is derived from declared architecture, not from a human remembering to write a test. `provenance` links each generated experiment back to its clause.
- **`UNTESTED` is reported**, so contract coverage is a metric (`chaosproof_contract_clause_coverage_ratio`) and gaps are visible rather than invisible.
- **A violated clause names the mechanism** where it can — the retry-amplification finding is the project's best single result.
- **The CI contract diff surfaces a weakening to its owners and does not auto-block.** Tooling that forces a conversation beats tooling that forces a merge failure.

> **GATE 9** — reproduce the retry-amplification finding: `order-api` claims it tolerates 5% from `payment-service`; inject exactly 5% loss; observe ~12% own error rate; the report names three retry attempts as the mechanism.

---

### Phase 10 — Week 10: counterfactual analysis

- **The feature-flag plane**, `enabled: false` by default, exposed only under the `chaos-staging` profile, never public. Every toggle emits `chaosproof_pattern_disabled{name}` and a log line so a cluster left degraded is visible rather than silent.
- **n ≥ 5 per arm, interleaved** (`with, without, with, without…`) so drift in cluster conditions affects both arms equally.
- **Overlapping IQRs → `inconclusive`**, delta suppressed. Reporting a median delta from overlapping distributions is how dashboards become fiction — and the restraint is itself the interview signal.
- **Staging only, enforced by policy** (`deny-counterfactual-outside-staging`). The "without" arm gets a **tighter** abort threshold: harder failure is expected, unbounded failure is not.
- **Flag snapshot restored by the cleanup saga**, CronJob as the out-of-band path. A cluster left with its circuit breakers disabled is the worst possible outcome of this project.
- **Counterfactual executions are excluded from the resilience score** — they are deliberately degraded configurations and would poison the trend.
- Cost assumptions are **editable inputs on the panel**, each labelled `[assumption]`.

> **GATE 10** — the ROI panel shows one pattern with non-overlapping IQRs and a delta, and one with overlapping IQRs rendering `inconclusive` with **no** delta figure. Both visible at once.

---

### Phase 11 — Week 11: cascades + evidence bundles + signing

- **Cascade DAG** with **conditional** triggers, not stopwatches. `db_pressure` fires only when DB p99 actually crosses 200ms (needs D-I's `db_query_seconds`). **If the trigger never trips within `timeout_s`, that is the most valuable output this feature produces**: the stale-cache fallback absorbed the outage completely, provable rather than assumed.
- **A stage with `fault: none`** — retry storms and queue growth appear *after* injection stops; a fault-then-immediately-validate loop misses them entirely.
- Aborting stage 2 while stage 1 is active halts **both**; cleanup runs stage cleanups in reverse dependency order.
- **Content-addressed evidence bundles** — the `sha256` of the canonical JSON *is* the run's identity. Write-once, immutable.
- **`chaosctl replay <sha>`** — offline, no cluster, no network, no database. Keep the output format stable; it doubles as a golden-file test and the dashboard renders it for the public read-only demo.
- **cosign keyless OIDC** signing of images and bundle attestations.
- **The narrator boundary**: `TemplateNarrator` is the **default** so the system works with no API key and no network — a portfolio project that breaks without a paid API is a liability in a live demo. `LLMNarrator` is provider-agnostic with the model id from config, its output discarded if it references any number not in the bundle, and discards counted in `chaosproof_narrator_discards_total`. **The LLM never touches a verdict.**

> **GATE 11** — `chaosctl replay <sha>` with wifi off produces byte-identical output to the stored bundle. This is demo beat 6.

---

### Phase 12 — Week 12: bisection, ADRs, polish

- **Bisection** with the σ arithmetic that makes it converge: required separation `|score_good − score_bad| > 2 × (σ_good + σ_bad)`; with σ=0.06 and a 0.16 regression, one rep fails (0.16 > 0.24 is false), three reps pass (σ_mean = 0.035 → 0.16 > 0.14). A candidate inside the flakiness band returns **`abandoned`**, never a guess — one misclassification sends the binary search down the wrong half permanently.
- **On-demand with a visible cost estimate**, hard candidate cap, nightly window. The Slack regression message carries a button: *"Bisect (est. 6 candidates, ~90 min)"*. A human decides. Automatic bisection on every score dip is a denial of service against the daily schedule.
- **Seven ADRs** in `docs/adr/`, per §27.
- **`EXPERIMENTS.md`** — the model card: per experiment, its hypothesis, fault, blast radius, abort conditions, σ, and gating status.
- **README** with the §27 architecture diagram, the honest §29.2 grid *including* the "where they win" paragraph, and the deliberate-omissions list.
- **Two-tier public deploy** — read-only build with trigger controls absent via a **build-time flag**, not merely disabled. A control that 404s invites someone to find out why.
- **Rehearse the §29.4 demo out loud.** The order is deliberate: **lead with your own bug.**

> **GATE 12** — the 5-minute demo runs end to end without a recovery, and the README's grid contains a paragraph naming where each competitor wins.

---

## Part 3 — Cross-cutting invariants

Consolidated from all eight skills, deduped. These hold in every phase.

**Measurement**
1. Load starts before pre-flight, which runs before injection — in CI too.
2. A missing or empty series is never a passing series.
3. Never widen a rate window to make a query "work." If the window must exceed the SLO to return data, the SLO is unmeasurable and *that is the finding*.
4. Never compute availability from server-side metrics alone.
5. `INVALID` never contributes to a score — not as zero, not as partial credit.
6. Never raise scrape frequency cluster-wide to fix one measurement.

**Safety**

7. Never inject before pre-flight returns `proceed`.
8. Never ship an experiment without `abort_conditions`.
9. Never let cleanup depend only on the happy path — the CronJob is not optional.
10. Never widen the policy file to make a run succeed. A denial is a result.
11. Never target the `chaosproof` namespace.
12. Never run counterfactual experiments outside `chaos-staging`.

**Scoring and quality**

13. Never change a weight, threshold, SLO, or the scorer without opening a new epoch.
14. Never `UPDATE` a stored score — new row, new epoch, same bundle.
15. Never let a new or reflagged experiment gate a merge before 20 clean runs.
16. Never delete a flaky experiment — quarantine and fix it.
17. Never add a scoring change without a corpus case proving the new behaviour.
18. Never let the narrator emit a number that isn't in the bundle.

**Target app**

19. Never remove `resilience4j-micrometer` or the startup assertion.
20. Never rename a pattern instance without updating the experiments that assert on it.
21. Never unset `metrics.tags.application`.
22. Never enable the flag plane outside `chaos-staging`.
23. Never use the `-spring-boot3` Resilience4j starter on Spring Boot 4.
24. Never add retry without accounting for amplification in the contract.

**Platform**

25. Never pin Litmus as `3.x` — pin the exact tag, and know why 3.28.0 and 3.29.0 matter.
26. Never use `:latest` anywhere, including compose and chart values.
27. Never grant the framework destructive verbs on the target — that is Litmus's job, scoped per experiment.
28. Never shorten `terminationGracePeriodSeconds` below what cleanup needs (900s).

**Dashboard**

29. Never style `invalid` as a failure or a pass.
30. Never connect a trend line across an epoch boundary.
31. Never show a counterfactual delta when the IQRs overlap.
32. Never render a bare score without its epoch.
33. Never ship trigger controls in the public build.

---

## Part 4 — Risk register

| Risk | Why it matters | Mitigation |
|---|---|---|
| **The three-skill asymmetry** | `INVALID` never scores (hypothesis-engine), what produces `INVALID` (measurement-integrity), and quarantine opening an epoch (mlops-quality) only make sense together. A session loading one could apply half the rule — e.g. treating `INVALID` as zero. | Part 1 of this document is the shared contract. If the split shows up in practice, promote the verdict vocabulary into a shared `references/` file rather than merging the skills. |
| **Resilience4j BOM omission (#2427)** | BOM-managed builds fail to resolve `resilience4j-spring-boot4`; the failure looks like a Spring Framework 7 incompatibility. | Verify the BOM in Phase 0; pin `${resilience4j.version}` explicitly on both modules if it is still missing. |
| **Litmus version drift** | Monthly cadence means any minor named here is a floor, not a target. 3.28.0 (same-type probe config leak) and 3.29.0 (duplicate triggers under concurrent reconciles) are load-bearing for the safety plane and serial execution respectively. | Pin whatever is current at install; assert the version in CI; never `3.x`. |
| **Node starvation producing `INVALID` runs** | The load plane plus 5s scraping needs real headroom. An experiment that fails because the *node* was starved produces `INVALID`, not insight. | `t3.large`, not `t3.medium`. Budget $0–70/month. |
| **The headline is in the air** | At KubeCon India, June 2026, the most common Litmus booth question was how to shift chaos left into CI — so the CI gate alone is no longer differentiating. | Lead with the verification layer, and specifically **flakiness quarantine** (§21.3): it is the answer to "why would your team still have this enabled in six months." |
| **xk6-disruptor** | A thin version of the load-plus-fault idea already exists inside k6. | Say so before an interviewer does — it is in §29.2's grid. |
| **Scale is the honest weakness** | Six experiments a day, three services, one cluster. No multi-tenancy, no chaos-injection HA, no VM chaos. | Q23's answer: Flipkart's KubeCon India talk covers exactly those four problems on top of Litmus, and reading it is how you know what you have not solved. Cite it as *"their talk describes…"*, not as your own analysis. |
| **`resilience4j-micrometer` silently absent** | Every pattern check reports `applicable=false` while the framework looks like it is working. | Gate 1 proves the startup assertion catches it. Keep that test. |

---

## Part 5 — Deferred, deliberately

- **`references/security-and-api.md`** — not warranted yet. The REST table, `chaosctl` surface, and metric list already live in `chaosproof-mlops-quality`'s reference file. The only genuine secret handling is one fine-grained GitHub PAT (issues + PR, one repo) for the contract-diff job, plus RBAC and cosign *keyless* signing — which by definition has no key material to scope. ChaosProof receives no inbound webhooks; it is a net consumer of Prometheus and Alertmanager. **What would change this:** authenticated dashboard actions beyond the read-only build, or a Slack interactive/slash-command endpoint needing signature verification. Until then, a short "Secrets and Scopes" section in `chaosproof-litmus-platform` is the right size.
- **Litmus MCP server** — one line in README future-work. Worth *not* building: a natural-language interface to fault injection is a safety surface, not a feature.
- **Multi-region, security chaos, game-day scoring** — `docs/future-work/` as ADRs.

---

## Part 6 — Worth testing next

Ask one of the extracted skills to add a new experiment — a DNS-chaos fault, say — and check whether `chaosproof-experiment-suite` actually forces a `min_rps_floor`, abort conditions, and advisory-only registration, or whether a plausible-looking experiment YAML slips through without them. That is the failure mode these skill descriptions are built to prevent, and it is cheap to verify.

---

*Prepared 23 August 2026. Codebase audit verified against working tree at commit `8dd5ef1`.*
