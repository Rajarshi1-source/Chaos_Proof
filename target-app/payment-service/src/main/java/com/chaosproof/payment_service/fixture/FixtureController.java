package com.chaosproof.payment_service.fixture;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.List;

/**
 * Chaos fixtures `cpu-burner` and `hungry-worker`. Gated behind
 * chaosproof.fixtures.enabled — absent from any public build, enabled only in
 * chaos namespaces. Fixture names match the `chaos_fixture` field on experiments.
 */
@RestController
@ConditionalOnProperty("chaosproof.fixtures.enabled")
public class FixtureController {

    private static final Logger log = LoggerFactory.getLogger(FixtureController.class);

    /** cpu-burner: tight loops on demand — the CPU-spike experiment's in-app trigger. */
    @PostMapping("/fixtures/cpu-burn")
    public String cpuBurn(@RequestParam(defaultValue = "30") int seconds,
                          @RequestParam(defaultValue = "2") int threads) {
        log.warn("chaos fixture cpu-burner: {} threads for {}s", threads, seconds);
        long until = System.currentTimeMillis() + seconds * 1000L;
        for (int i = 0; i < threads; i++) {
            var t = new Thread(() -> {
                double sink = 0;
                while (System.currentTimeMillis() < until) {
                    sink += Math.sqrt(sink + 1);   // busy work the JIT cannot fully elide
                }
            }, "cpu-burner-" + i);
            t.setDaemon(true);
            t.start();
        }
        return "burning " + threads + " threads for " + seconds + "s";
    }

    /** hungry-worker: allocates until the container OOMs. Deliberately unbounded. */
    @PostMapping("/fixtures/oom")
    public String hungryWorker(@RequestParam(defaultValue = "16") int mbPerStep) {
        log.warn("chaos fixture hungry-worker: allocating {}MB steps until OOM", mbPerStep);
        var t = new Thread(() -> {
            List<byte[]> hoard = new ArrayList<>();
            while (true) {
                hoard.add(new byte[mbPerStep * 1024 * 1024]);
            }
        }, "hungry-worker");
        t.setDaemon(true);
        t.start();
        return "allocating until OOM";
    }
}
