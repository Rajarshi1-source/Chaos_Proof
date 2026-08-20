---
name: chaosproof-litmus-platform
description: "Build and maintain ChaosProof's Kubernetes platform: everything outside the decision logic. Use for ANY work on LitmusChaos install and ChaosEngine CRDs, Prometheus and Alertmanager config, the k6 Operator, least-privilege RBAC, Helm 4 charts, kind clusters, docker-compose, the GitHub Actions pipeline and its merge gates, Grafana dashboards as JSON, and deployment strategy including advisory-mode canary, expand-contract migrations, and the read-only public demo. Trigger on LitmusChaos, ChaosEngine, chaos-charts, kube-prometheus-stack, ServiceMonitor, PrometheusRule, k6 Operator, RBAC, ClusterRole, Helm, kind, kindest/node, docker-compose, GitHub Actions, CI gate, cosign, or Grafana. MANDATE: pin Litmus to an exact tag (3.28.0 fixed same-type probe config leaks, 3.29.0 fixed duplicate triggers under concurrent reconciles), Kubernetes 1.36 with a 1.35 floor for cgroup v2, Helm 4.2.x, never latest, and the load plane starts before the experiment runner."
---

# ChaosProof Kubernetes Platform

Everything outside the framework's decision logic: the fault injector, the observability stack, the load operator, RBAC, charts, CI, and deployment.

## Version floor — verified 19 August 2026

| Component | Pin | Why this exact thing matters |
|---|---|---|
| **LitmusChaos** | **3.30.0 or later — pin the exact monthly tag** | Litmus ships monthly (3.25.0 Jan → 3.30.0 Jun 2026), so `3.x` is not a pin. **Expect a higher minor by install time; pin whatever is current.** Three recent fixes are load-bearing — see below |
| **Kubernetes** | **1.36** demo (`kindest/node:v1.36.x`), **1.35 floor** | 1.34 entered maintenance Aug 2026. 1.35+ requires **cgroup v2**, which is what makes throttle-ratio and PSI signals trustworthy — this project lives on those signals. PSI GA in 1.36 |
| **k6** | **2.2.x** + **k6 Operator v1.0** | The load plane. k6 2.0 (May 2026) added OpenTelemetry output, structured JSON, and a `run-k6-action` GitHub Action; the Operator runs distributed load in-cluster as a `TestRun` CR |
| **Prometheus** | **3.x, chart-pinned** via `kube-prometheus-stack` (explicit chart version) | Never `latest`. **Override `scrapeInterval` to 5s for the target app's ServiceMonitor only** — the 30s default cannot resolve a 30s recovery SLO |
| **Alertmanager** | **0.33.1** | **API v1 removed in 0.27** — the alert validator must use `/api/v2/alerts` |
| **Grafana** | **13.0.x** (13.0.0 released 14 Apr 2026), dashboards provisioned as JSON in-repo | Reproducibility; `:latest` in a resilience project is self-refuting |
| **PostgreSQL** | **18.6** (`postgres:18.6-alpine`) | `uuidv7()`; PG 19 is beta — never ship a beta database |
| **Cache/queue** | **Valkey 9.1** (`valkey/valkey:9-alpine`) or Redis 8.x | Streams are the queue |
| **Helm** | **4.2.x** | Helm 3 bug fixes ended 8 July 2026 |
| **Python / Node** | **3.14** / **24 LTS** | Framework and dashboard runtimes |

### The three Litmus fixes to know by name

| Version | Fix | Why ChaosProof depends on it |
|---|---|---|
| **3.29.0** | Duplicate chaos experiment triggers under concurrent reconciles | **Serial execution is the project's core scaling decision.** Before this fix, that guarantee partly depended on luck |
| 3.29.0 | Stopping experiments when infra is disconnected; Litmus Prometheus metrics | Abort semantics; platform self-observability |
| **3.28.0** | Stale config leak across **multiple probes of the same type** | The safety plane uses several `promProbe`s per experiment — directly load-bearing |
| 3.27.0 | Returns 503 when the DB is down so **probe detection stays accurate**; CMD-probe 1024-char limit removed; Job targeting | Correct probe behaviour under degradation |

### Helm 4 gotchas

1. **Server-side apply is the default for new installs** — expect explicit conflict errors instead of silent overwrites.
2. **`--wait` needs the `watch` RBAC verb** in the CI service account's role. This breaks CI silently on upgrade if missed.
3. **`--post-renderer` must now be a plugin.**

### Pin every tag, including the easy-to-forget ones

A document that mandates pinning while shipping `:latest` is the first thing a sharp reviewer notices. When touching `docker-compose.yml` or chart values, check for and fix:

- `grafana/grafana:latest` → `grafana/grafana:13.0.0`
- The obsolete top-level `version: '3.8'` key — Compose v2 ignores it and warns
- Framework and dashboard image tags → the git SHA that CI pushes
- **A missing k6 service in docker-compose** — a local stack with no load generator reproduces the project's original defect on the developer's laptop

## RBAC — least privilege, justified verb by verb

```yaml
rules:
  - apiGroups: ["litmuschaos.io"]
    resources: ["chaosengines","chaosexperiments","chaosresults"]
    verbs: ["get","list","watch","create","delete"]
  - apiGroups: ["k6.io"]
    resources: ["testruns"]
    verbs: ["get","list","create","delete"]
  - apiGroups: [""]
    resources: ["pods","pods/log","events"]
    verbs: ["get","list","watch"]
  - apiGroups: ["apps"]
    resources: ["deployments","deployments/scale"]
    verbs: ["get","list","watch","patch"]        # patch = restore replicas in cleanup
  - apiGroups: ["autoscaling"]
    resources: ["horizontalpodautoscalers"]
    verbs: ["get","list"]                        # CPU-spike needs to know if an HPA owns it
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["get","create","update"]
```

Deliberately absent — **this is the interview answer**: no `delete` on deployments or PVCs, no `secrets` read outside its own namespace, no RBAC write, no wildcards, no cluster-admin.

> *"The one mutating permission my framework holds on the target is `deployments/scale`, and it exists so cleanup can put replica counts back the way it found them. Everything destructive is done by Litmus's own service account inside the experiment, scoped per-experiment and gone with it. My framework observes and cleans up — the fault injector is the thing with teeth, and it's a CNCF project I didn't write."*

ChaosProof's own namespace carries `chaosproof.io/chaos: disabled`, and the policy file denies any experiment targeting it.

## Prometheus and Alertmanager

- **`scrapeInterval: 5s` on the target app's ServiceMonitor only**, with `scrapeTimeout` strictly less than the interval. Cluster-wide 5s scraping multiplies load for no benefit — scoping it shows you understand the cost of your own instrumentation.
- **k6 remote-writes into the same Prometheus**, because the safety-plane probes query k6's series. A guard reading server-side metrics cannot see failures that never reached a server.
- **≥30 days retention**, because the error-budget window is 30 days. Check at startup and refuse to gate on an incomputable budget.
- Alert rules as a `PrometheusRule` CR, five rules matching the experiments. **Every rule is linted in CI** against the irreducible-latency arithmetic (`chaosproof-measurement-integrity`) — a rule whose `for:` plus evaluation interval plus scrape interval exceeds its own latency SLO can never pass, and the build fails on it.
- **Alertmanager API v2 only.**

## Local and CI clusters

`kind` 1.36 with the target app, kube-prometheus-stack, Litmus, and the k6 Operator. `make kind-up` should produce a cluster where `make experiment NAME=pod_kill_payment_svc` works end to end — including load. A local setup that cannot generate load teaches the wrong habit.

## CI pipeline

```yaml
jobs:
  unit:
    - run: pytest -m "not chaos" --cov=chaos-framework --cov-fail-under=80

  replay-eval:               # hermetic, seconds; catches every fixed defect
    - run: python -m evals.replay_eval
    - run: python -m evals.policy_eval
    - run: python -m evals.alert_rule_lint

  contract-gate:
    - run: python -m chaos_framework.contracts.diff --base ${{ github.base_ref }}

  chaos-gate:                # real kind cluster, real faults, real load
    - uses: helm/kind-action@v1
      with: { node_image: "kindest/node:v1.36.0" }
    - run: helm install kps prometheus-community/kube-prometheus-stack --version <PINNED>
    - run: helm install litmus litmuschaos/litmus --version <PINNED>
    - run: helm install target-app charts/target-app --set replicas=3
    - run: kubectl apply -f k6/testrun-ci.yaml     # LOAD FIRST
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

Five things to preserve:

1. **Load starts before the experiment runner.** A chaos gate with no load plane reproduces the original defect inside the pipeline, where a green badge hides it. This is the single most important line in the file.
2. **Separate gate jobs** so a failure names its own cause.
3. **The chaos job needs a real kind cluster** with chart versions pinned.
4. **Cluster dump on failure** is what makes a flaky chaos test debuggable.
5. **`build-sign-deploy` needs all four gates**, and the chaos gate only blocks on experiments that have earned gating status through flakiness characterisation.

Keep the unit and eval gates fast and independent — a developer should be able to fail cheaply before waiting on a cluster.

## Grafana

Grafana 13, dashboards as JSON in `monitoring/grafana/`, provisioned rather than clicked. Panels the platform owns: resilience gauge and epoch-aware trend; per-experiment verdict history; **SLI-validity strip**; client-vs-server availability overlay; recovery timeline with the irreducible-latency marker; error-budget gauge with gate state; flakiness table with quarantined experiments marked; `dropped_iterations`; and Litmus's own metrics (3.29.0+) so a silent platform failure is distinguishable from "everything passed."

## Deployment

| Environment | Cluster | Chaos posture |
|---|---|---|
| local | `kind` 1.36 + target app + k6 | All advisory |
| ci | ephemeral `kind` per PR | 2 gating experiments + load |
| staging | small cloud cluster | All 6 + cascades + **counterfactuals** (the only place patterns are disabled) |
| prod (demo) | small GKE/EKS | Daily 6, low-radius, budget-gated |

- **Rolling update, `maxUnavailable: 0`**, readiness gated on PostgreSQL, Redis, Prometheus, and the K8s API. A framework that starts without its measurement plane produces `INVALID` runs and burns the schedule.
- **`helm upgrade --install --atomic --wait`**; remember the Helm 4 `watch` verb.
- **Never deploy during an experiment.** The scheduler holds a deploy lease; the deploy job waits for `running` executions or times out. An interrupted execution is an `INVALID` run *and* an orphaned fault.
- **`terminationGracePeriodSeconds: 900`** — long, deliberately. On `SIGTERM`: stop consuming the queue, finish the in-flight experiment **including cleanup**, release the Lease. A 60-second grace period would routinely orphan faults.
- **Expand-contract migrations**, forward-only via Alembic.
- **Canary via advisory mode:** deploy the new version with all experiments forced advisory for one nightly staging cycle, then **diff its verdicts against the previous version's on the same bundles** — the replay eval makes this mechanical. That is regression testing for a scoring system.
- **Two promotion pipelines, deliberately separate:** software promotes by `git push → CI → Helm`; an experiment's authority to block a merge promotes through flakiness characterisation. Shipping a new framework version never silently grants an experiment gating power.

### Public deployment — two-tier

1. **Read-only dashboard** (Vercel or ingress), seeded with real recorded executions plus rendered `chaosctl replay` output. No cluster credentials, no trigger path. Manual-trigger controls are **absent from the read-only build via a build-time flag**, not merely disabled — a control that 404s invites someone to find out why.
2. **The real framework**, only in the demo cluster, behind auth.

**Never expose an endpoint that can inject a fault to the internet.** Say it before being asked: *"the public demo is read-only by design; the only path that can inject a fault runs inside the cluster."*

### Cost

GKE Autopilot small cluster ~$70/mo, or kind on an EC2 `t3.large` ~$60/mo, or local kind $0. Note `t3.large` rather than `t3.medium`: the load plane and 5-second scraping need real headroom, and an experiment that fails because the *node* was starved produces `INVALID` runs, not insight.

## Non-negotiables

1. **Never pin Litmus as `3.x`.** Pin the exact tag and know why 3.28.0 and 3.29.0 matter.
2. **Never run a chaos gate without the load plane started first.**
3. **Never use `:latest`** anywhere, including compose and chart values.
4. **Never raise scrape frequency cluster-wide** to fix one measurement.
5. **Never grant the framework destructive verbs** on the target — that is Litmus's job, scoped per experiment.
6. **Never shorten the grace period** below what cleanup needs.
