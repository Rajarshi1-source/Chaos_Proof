package com.chaosproof.inventory_service.resilience;

import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.retry.RetryRegistry;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Fail loudly, not silently. Throwing here aborts startup, so the pod never becomes
 * ready and a rolling update cannot replace a working pod with a blind one.
 */
@Component
@Order(2)   // after the data seeder
public class ResilienceMetricsAssertion implements ApplicationRunner {

    private static final List<String> REQUIRED = List.of(
            "resilience4j.circuitbreaker.state",
            "resilience4j.circuitbreaker.calls",
            "resilience4j.retry.calls");

    private final MeterRegistry registry;
    private final CircuitBreakerRegistry circuitBreakers;
    private final RetryRegistry retries;
    private final Environment env;

    public ResilienceMetricsAssertion(MeterRegistry registry, CircuitBreakerRegistry circuitBreakers,
                                      RetryRegistry retries, Environment env) {
        this.registry = registry;
        this.circuitBreakers = circuitBreakers;
        this.retries = retries;
        this.env = env;
    }

    @Override
    public void run(ApplicationArguments args) {
        // Warm-up: meters only register once an instance exists.
        circuitBreakers.circuitBreaker("cache");
        retries.retry("database");

        var missing = REQUIRED.stream()
                .filter(n -> registry.find(n).meters().isEmpty())
                .toList();
        if (!missing.isEmpty()) {
            throw new IllegalStateException(
                    "Resilience metrics missing: " + missing + ". ChaosProof cannot validate "
                    + "patterns it cannot observe. Is resilience4j-micrometer on the classpath?");
        }

        var app = env.getProperty("spring.application.name", "unknown");
        if (registry.find("resilience4j.circuitbreaker.state").tag("application", app).meters().isEmpty()) {
            throw new IllegalStateException(
                    "resilience4j.circuitbreaker.state lacks the application=" + app
                    + " tag — every ChaosProof query would silently return empty.");
        }
    }
}
