---
name: chaosproof-spring-target-app
description: "Build ChaosProof's target application, the subject under test: Spring Boot 4.1.x with Resilience4j 3.x on Java 21. Use for ANY work on the three services and their named pattern instances, Resilience4j configuration and Micrometer metric exposure, the startup assertion that fails readiness when pattern metrics are missing, the counterfactual feature-flag plane, resilience contract authoring, chaos fixtures, and ephemeral-storage and CPU limits. Trigger on Spring Boot 4, Resilience4j, circuit breaker, bulkhead, rate limiter, retry, fallback, Actuator, Micrometer, resilience4j-spring-boot4, resilience4j-micrometer, feature flag, chaos fixture, or Java 21. MANDATE: this project can only validate patterns that publish their state. resilience4j-micrometer is NOT transitive and without it every pattern check silently validates nothing. Use the resilience4j-spring-boot4 artifact, never -spring-boot3, and pin it explicitly if the BOM omits it."
---

# ChaosProof Target Application — Spring Boot 4 + Resilience4j 3

The target app is not incidental scaffolding; it is **the subject under test**. Its resilience patterns are the things ChaosProof validates, which imposes one governing constraint absent from a normal Spring service:

> **This project can only validate patterns that publish their state.** An unobservable circuit breaker is unverifiable, and an unverifiable pattern is exactly what ChaosProof exists to catch.

Every dependency decision below follows from that.

## Version floor

| Component | Pin | Why |
|---|---|---|
| Java | **21 LTS** | **Resilience4j 3 requires Java 21** (Resilience4j 2 requires 17) |
| Spring Boot | **4.1.x** — take whatever patch Initializr offers on the day you generate | The *line* is what matters (never 4.0.x — loses OSS support Dec 2026, and 4.0.6 specifically has 7 CVEs patched in 4.0.7); the exact patch digit isn't load-bearing and ships roughly monthly (4.1.0 → 4.1.1 within days of each other). Bumping later is a one-line edit — `<parent><version>` in each `pom.xml`. **Portfolio-consistency fallback:** to match NaukriNearby exactly, use **4.0.7** (the patched 4.0.x, never 4.0.6) — everything else on this page is identical either way |
| Resilience4j | **`resilience4j-spring-boot4` 2.4.0** — the only Boot-4 line published on Maven Central as of Aug 2026; move to 3.x when it actually ships | See the two traps below |
| Metrics | **`resilience4j-micrometer`** + `micrometer-registry-prometheus` + `spring-boot-starter-actuator` | Not optional. **Runtime-verified nuance:** `-spring-boot4` 2.4.0 *does* pull `resilience4j-micrometer` transitively (runtime scope) — the not-transitive trap was real for `-spring-boot3`. Keep the explicit pin anyway (guards against the transitive being dropped again, and the BOM omission #2427 is still real), and treat the startup assertion as the enforced guard |
| AOP | `org.aspectj:aspectjweaver` — **add manually to `pom.xml`; `spring-boot-starter-aop` was REMOVED in Boot 4 GA** (verified: the starter's last published version is 4.0.0-M2, and the 4.1.x parent manages `aspectjweaver` directly) | Required for Resilience4j's `@Aspect`-based annotations. Don't assume Spring Data JPA or Spring Security pull this in for you — core proxy-based AOP is transitively present via `spring-context`, but Resilience4j's autoconfigured aspects need AspectJ weaving support, which is a different thing. Version comes from the Boot parent. Same rule as `resilience4j-micrometer` below: declare it, don't gamble on a transitive |
| Build | **Maven**, `maven.compiler.release` pinned to 21 | Matches the plan's own `pom.xml` project layout (§5.1) — Gradle was listed here in error in an earlier draft of this skill |

### Two build traps that cost hours

1. **The artifact is `resilience4j-spring-boot4`, not `-spring-boot3`.** Spring Boot 4 support arrived in the Resilience4j **2.4.0** line via a *new* artifact. Using the `-spring-boot3` starter on Boot 4 fails in ways that look like Spring Framework 7 incompatibility rather than a wrong dependency.
2. **That artifact was omitted from the Resilience4j BOM** (issue #2427, March 2026). BOM-managed builds fail to resolve it. Verify the BOM includes it; otherwise **pin the module version explicitly**:

```xml
<dependencies>
  <dependency>
    <groupId>io.github.resilience4j</groupId>
    <artifactId>resilience4j-spring-boot4</artifactId>
    <version>${resilience4j.version}</version>   <!-- pin explicitly if the BOM omits it -->
  </dependency>
  <dependency>
    <groupId>io.github.resilience4j</groupId>
    <artifactId>resilience4j-micrometer</artifactId>
    <version>${resilience4j.version}</version>   <!-- NOT transitive -->
  </dependency>
  <dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
  </dependency>
  <dependency>
    <groupId>org.aspectj</groupId>
    <artifactId>aspectjweaver</artifactId>   <!-- Boot 4 dropped spring-boot-starter-aop; parent manages the version -->
  </dependency>
  <dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
  </dependency>
</dependencies>
```

**`resilience4j-micrometer` is the one that silently ruins the project.** Without it, no `resilience4j_*` series exist, every pattern check in ChaosProof reports `applicable=false`, and the framework looks like it is working while validating nothing. Fail readiness if the series are absent (see below). **Runtime-verified (Gate 1 negative test):** `-spring-boot4` 2.4.0 ships it transitively at runtime scope, so merely deleting the explicit dependency does *not* blind the app — but an `<exclusion>`, a future artifact change, or a `-spring-boot3` classpath does, and the startup assertion caught the severed classpath exactly as designed.

## Why not Spring Boot 4's native resilience

Spring Boot 4 ships `@Retryable`, `@ConcurrencyLimit`, and `@EnableResilientMethods`, which removes the need for Resilience4j in many applications. For this project it is the wrong choice:

| | Spring Boot 4 native | **Resilience4j 3.x** |
|---|---|---|
| Retry | ✅ `@Retryable` | ✅ |
| Concurrency limiting | ✅ `@ConcurrencyLimit` | ✅ bulkhead |
| **Circuit breaker** | ❌ **not provided** | ✅ with observable state machine |
| Rate limiter | ❌ | ✅ |
| **Prometheus state metrics** | Limited; no `*_state` gauge to read | ✅ `resilience4j_circuitbreaker_state`, `_calls_total`, `resilience4j_retry_calls_total`, `resilience4j_bulkhead_available_concurrent_calls` |
| Verdict here | Rejected | **Chosen** |

The circuit breaker is the single most important pattern ChaosProof validates, and native Spring does not have one. Use Boot 4's `@Retryable` where retry is wanted *without* verification; use Resilience4j everywhere the validator must see inside.

> *"I stayed on Resilience4j rather than Spring Boot 4's native retry for a specific reason: native Spring resilience has no circuit breaker and doesn't emit the metrics my validator reads. I chose the dependency for its observability. An unobservable circuit breaker is unverifiable, and an unverifiable pattern is exactly what this project exists to catch."*

## The three services and their patterns

| Service | Patterns | Why these |
|---|---|---|
| `order-api` (gateway) | **Circuit breaker** → payment-service, **Retry** → inventory-service, global 3s **Timeout** | A gateway must degrade gracefully: CB prevents cascade, retry absorbs transient failures |
| `payment-service` | **Bulkhead** (isolate processing threads), **Rate limiter** (100 rps), **Fallback** (queue for retry) | Payment is critical: bulkhead prevents thread starvation, fallback queues for async processing |
| `inventory-service` | **Retry** → database, **Circuit breaker** → cache, **Stale-cache fallback** | Read-heavy: retries absorb DB blips, stale cache is an acceptable degradation |

Each pattern must be **individually addressable by name**, because experiments assert on specific instances:

```yaml
resilience4j:
  circuitbreaker:
    instances:
      paymentService:
        registerHealthIndicator: true
        slidingWindowSize: 10
        failureRateThreshold: 50
        waitDurationInOpenState: 30s
  retry:
    instances:
      inventoryService:
        maxAttempts: 3            # see the retry-amplification warning below
        waitDuration: 500ms
  bulkhead:
    instances:
      paymentProcessing:
        maxConcurrentCalls: 25

management:
  endpoints.web.exposure.include: health,prometheus,metrics
  metrics.tags.application: ${spring.application.name}
```

`metrics.tags.application` matters: ChaosProof's queries filter by `application=`, so an unset tag makes every pattern query return empty — the failure mode that looks like a passing test.

### Retry amplification — the project's best finding

`maxAttempts: 3` against a dependency with a 5% error rate produces roughly **14% effective load and observed error rate**, because retries multiply the failing traffic. ChaosProof's contract system found exactly this: `order-api` claimed it tolerated 5% errors from `payment-service` and its own error rate hit 12.1%.

Preserve the ability to reproduce it. When adding retry to any call path, either cap attempts at 2, add a retry budget, or **declare the amplified rate in the contract** rather than the upstream rate. This is a real distributed-systems failure mode, not a configuration nit.

## Startup assertion — fail loudly, not silently

```java
@Component
class ResilienceMetricsAssertion implements ApplicationRunner {
    private static final List<String> REQUIRED = List.of(
        "resilience4j.circuitbreaker.state",
        "resilience4j.circuitbreaker.calls",
        "resilience4j.retry.calls",
        "resilience4j.bulkhead.available.concurrent.calls");

    private final MeterRegistry registry;

    @Override public void run(ApplicationArguments args) {
        var missing = REQUIRED.stream()
            .filter(n -> registry.find(n).meters().isEmpty())
            .toList();
        if (!missing.isEmpty()) {
            throw new IllegalStateException(
                "Resilience metrics missing: " + missing + ". ChaosProof cannot validate "
                + "patterns it cannot observe. Is resilience4j-micrometer on the classpath?");
        }
    }
}
```

Refusing to start is correct here. A target app that runs without publishing its pattern state turns every chaos experiment into a false pass — the worst outcome in this project. Wire it into readiness so a rolling update cannot replace a working pod with a blind one.

Note that some meters only register after their instance is first exercised. Either pre-register instances at startup or run a synthetic warm-up call before asserting.

## The feature-flag plane (for counterfactual experiments)

Counterfactual analysis needs each pattern toggled off at runtime. This is the most dangerous surface in the app, so constrain it:

```java
// Toggle by pattern instance name. Must be a no-op unless explicitly enabled.
@ConfigurationProperties("chaosproof.flags")
class ResilienceFlags {
    private boolean enabled = false;                 // default OFF
    private Set<String> disabledPatterns = Set.of();
}
```

Non-negotiables:

- **`enabled: false` by default**, and the flag endpoint is not exposed unless the app runs with the `chaos-staging` profile. Policy denies counterfactual runs outside that namespace, but defence in depth belongs in the app too.
- **Every toggle emits a metric and a log line** — `chaosproof_pattern_disabled{name}` — so a cluster left in a degraded configuration is visible rather than silent.
- **The flag state is snapshotted before a counterfactual run and restored by the cleanup saga**, with the cleanup CronJob as the out-of-band path. A cluster left with its circuit breakers disabled is the worst possible outcome of this project.
- **Never expose the flag endpoint publicly.** It is a switch that removes production protection.

## The resilience contract

Each service ships `resilience-contract.yaml` declaring what it provides, what it tolerates from each dependency, and what it pledges not to inflict. ChaosProof generates one experiment per `tolerates` clause. Authoring rules:

- **Declare the amplified rate**, not the raw upstream rate, wherever retry sits in the path.
- **Every `graceful_degradation_mode` must be observable** — if a fallback fires, something must publish that it fired, or the contract clause is untestable.
- **Bump the contract version on any weakening**; CI surfaces the diff to clause owners.

Schema and generator live in `chaosproof-experiment-suite`.

## Chaos fixtures

Each service exposes a deliberate fault trigger matching one experiment: `crashy-api` (`CRASH_AFTER=30s`), `hungry-worker` (allocates until OOM), `cpu-burner` (tight loop on demand), `log-spammer` (writes to fill ephemeral storage). The fixture name is the `chaos_fixture` value on the experiment, so keep them in sync.

**Set `ephemeral-storage` limits on any pod targeted by the disk-fill experiment** — without limits, `disk-fill` has nothing to exceed and the experiment silently does nothing:

```yaml
resources:
  limits:   { ephemeral-storage: 1Gi, memory: 512Mi, cpu: 500m }
  requests: { ephemeral-storage: 512Mi, memory: 256Mi, cpu: 200m }
```

CPU limits interact with the CPU-hog experiment (the hog gets throttled rather than raising utilisation) — that is the scaling paradox documented in `chaosproof-experiment-suite`, and it is why that experiment asserts throttle ratio rather than "the spike went away."

## Modern Java 21 usage

Virtual threads for the blocking downstream calls (`spring.threads.virtual.enabled=true`) — note this changes thread-pool behaviour that bulkhead metrics report on, so re-baseline flakiness (`chaosproof-mlops-quality`) after enabling it. Records for DTOs and fallback payloads; sealed interfaces plus pattern matching for result/error types; text blocks for native SQL.

## Non-negotiables

1. **Never remove `resilience4j-micrometer`** or the startup assertion.
2. **Never rename a pattern instance** without updating the experiments that assert on it — the query silently returns empty, which reads as a pass.
3. **Never unset `metrics.tags.application`.**
4. **Never enable the flag plane outside `chaos-staging`.**
5. **Never use the `-spring-boot3` Resilience4j starter on Spring Boot 4.**
6. **Never add retry without accounting for amplification** in the contract.
