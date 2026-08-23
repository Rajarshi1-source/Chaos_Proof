package com.chaosproof.order_api.fixture;

import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Chaos fixture `crashy-api`: if CRASH_AFTER is set (e.g. "30s"), the process
 * exits after that delay. Inert unless the env var is present — the fixture name
 * is the `chaos_fixture` value on the pod-kill experiment.
 */
@Component
public class CrashyFixture {

    private static final Logger log = LoggerFactory.getLogger(CrashyFixture.class);

    @Value("${CRASH_AFTER:}")
    private String crashAfter;

    @PostConstruct
    void arm() {
        if (crashAfter == null || crashAfter.isBlank()) {
            return;
        }
        var delay = Duration.parse("PT" + crashAfter.toUpperCase());
        log.warn("chaos fixture crashy-api ARMED: exiting in {}", delay);
        Executors.newSingleThreadScheduledExecutor(r -> {
            var t = new Thread(r, "crashy-api");
            t.setDaemon(true);
            return t;
        }).schedule(() -> {
            log.error("chaos fixture crashy-api: crashing NOW");
            System.exit(1);
        }, delay.toMillis(), TimeUnit.MILLISECONDS);
    }
}
