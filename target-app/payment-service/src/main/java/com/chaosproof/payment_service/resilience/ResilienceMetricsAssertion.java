package com.chaosproof.payment_service.resilience;

import io.github.resilience4j.bulkhead.BulkheadRegistry;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Fail loudly, not silently. Throwing here aborts startup, so the pod never becomes
 * ready and a rolling update cannot replace a working pod with a blind one.
 */
@Component
public class ResilienceMetricsAssertion implements ApplicationRunner {

    private static final List<String> REQUIRED = List.of(
            "resilience4j.bulkhead.available.concurrent.calls",
            "resilience4j.ratelimiter.available.permissions");

    private final MeterRegistry registry;
    private final BulkheadRegistry bulkheads;
    private final RateLimiterRegistry rateLimiters;
    private final Environment env;

    public ResilienceMetricsAssertion(MeterRegistry registry, BulkheadRegistry bulkheads,
                                      RateLimiterRegistry rateLimiters, Environment env) {
        this.registry = registry;
        this.bulkheads = bulkheads;
        this.rateLimiters = rateLimiters;
        this.env = env;
    }

    @Override
    public void run(ApplicationArguments args) {
        // Warm-up: meters only register once an instance exists. Touch this service's
        // named instances so absence below means a broken classpath, never lazy registration.
        bulkheads.bulkhead("paymentProcessing");
        rateLimiters.rateLimiter("paymentApi");

        var missing = REQUIRED.stream()
                .filter(n -> registry.find(n).meters().isEmpty())
                .toList();
        if (!missing.isEmpty()) {
            throw new IllegalStateException(
                    "Resilience metrics missing: " + missing + ". ChaosProof cannot validate "
                    + "patterns it cannot observe. Is resilience4j-micrometer on the classpath?");
        }

        // The application= tag is what every ChaosProof query filters on.
        var app = env.getProperty("spring.application.name", "unknown");
        if (registry.find("resilience4j.bulkhead.available.concurrent.calls")
                .tag("application", app).meters().isEmpty()) {
            throw new IllegalStateException(
                    "bulkhead meter lacks the application=" + app
                    + " tag — every ChaosProof query would silently return empty.");
        }
    }
}
