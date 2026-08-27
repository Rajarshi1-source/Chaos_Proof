package com.chaosproof.order_api.resilience;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.MultiGauge;
import io.micrometer.core.instrument.Tags;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArraySet;

/**
 * The feature-flag plane: toggles a named resilience pattern OFF at runtime so a
 * counterfactual run can measure what the pattern is actually worth.
 *
 * This is the most dangerous surface in the application — it removes production
 * protection — so it is constrained on every axis the skill names:
 *
 *   - `enabled` defaults to FALSE. A flag that is merely listed does nothing.
 *   - The HTTP endpoint (see FlagController) only exists under the `chaos-staging`
 *     profile. Policy already denies counterfactual runs outside that namespace;
 *     defence in depth belongs in the app too.
 *   - Every disabled pattern publishes `chaosproof_pattern_disabled{name}` AND a
 *     WARN log line, so a cluster left in a degraded configuration is visible
 *     rather than silent. A flag you cannot see is worse than no flag.
 *   - The runner snapshots this state before a counterfactual run and the cleanup
 *     saga restores it, with the chaos-cleanup CronJob as the out-of-band path.
 */
@Component
@ConfigurationProperties("chaosproof.flags")
public class ResilienceFlags {

    private static final Logger log = LoggerFactory.getLogger(ResilienceFlags.class);

    private boolean enabled = false;                        // default OFF
    private final Set<String> disabledPatterns = new CopyOnWriteArraySet<>();
    private final MultiGauge disabledGauge;

    public ResilienceFlags(MeterRegistry registry) {
        this.disabledGauge = MultiGauge.builder("chaosproof.pattern.disabled")
                .description("1 while a named resilience pattern is deliberately disabled")
                .register(registry);
        publish();
    }

    /** The only question callers ask. False unless the plane is explicitly enabled. */
    public boolean isDisabled(String pattern) {
        return enabled && disabledPatterns.contains(pattern);
    }

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
        log.warn("chaosproof flag plane enabled={}", enabled);
        publish();
    }

    public Set<String> getDisabledPatterns() {
        return Set.copyOf(disabledPatterns);
    }

    public void setDisabledPatterns(Set<String> patterns) {
        disabledPatterns.clear();
        if (patterns != null) {
            disabledPatterns.addAll(patterns);
        }
        if (!disabledPatterns.isEmpty()) {
            log.warn("RESILIENCE PATTERNS DISABLED: {} — this cluster is deliberately "
                     + "degraded and must be restored by the cleanup saga", disabledPatterns);
        }
        publish();
    }

    /** Snapshot for the cleanup saga to restore. */
    public Snapshot snapshot() {
        return new Snapshot(enabled, Set.copyOf(disabledPatterns));
    }

    public void restore(Snapshot s) {
        this.enabled = s.enabled();
        disabledPatterns.clear();
        disabledPatterns.addAll(s.disabledPatterns());
        log.warn("flag plane restored to enabled={} disabled={}", s.enabled(), s.disabledPatterns());
        publish();
    }

    private void publish() {
        disabledGauge.register(
                disabledPatterns.stream()
                        .map(p -> MultiGauge.Row.of(Tags.of("name", p), 1.0))
                        .toList(),
                true);
        if (disabledPatterns.isEmpty()) {
            disabledGauge.register(List.of(), true);
        }
    }

    public record Snapshot(boolean enabled, Set<String> disabledPatterns) {}
}
