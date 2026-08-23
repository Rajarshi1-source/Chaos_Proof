package com.chaosproof.inventory_service.fixture;

import com.chaosproof.inventory_service.cache.CacheClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Chaos fixtures `log-spammer` (fills ephemeral storage — pairs with the disk-fill
 * experiment and the pod's ephemeral-storage limit) and `cache-kill` (opens the
 * `cache` circuit breaker deterministically). Gated behind chaosproof.fixtures.enabled.
 */
@RestController
@ConditionalOnProperty("chaosproof.fixtures.enabled")
public class FixtureController {

    private static final Logger log = LoggerFactory.getLogger(FixtureController.class);

    private final CacheClient cache;

    public FixtureController(CacheClient cache) {
        this.cache = cache;
    }

    /** log-spammer: writes junk into ephemeral storage until told how much. */
    @PostMapping("/fixtures/log-spam")
    public String logSpam(@RequestParam(defaultValue = "100") int mb) throws IOException {
        var dir = Path.of(System.getProperty("java.io.tmpdir"), "chaos-spam");
        Files.createDirectories(dir);
        var chunk = new byte[1024 * 1024];
        log.warn("chaos fixture log-spammer: writing {}MB to {}", mb, dir);
        for (int i = 0; i < mb; i++) {
            Files.write(dir.resolve("spam-" + System.nanoTime() + ".log"), chunk);
        }
        return "wrote " + mb + "MB to " + dir;
    }

    /** cache-kill: makes every cache call throw until restored — the cache-outage trigger. */
    @PostMapping("/fixtures/cache")
    public String cacheAvailability(@RequestParam boolean available) {
        cache.setAvailable(available);
        log.warn("chaos fixture cache-kill: cache available={}", available);
        return "cache available=" + available;
    }
}
