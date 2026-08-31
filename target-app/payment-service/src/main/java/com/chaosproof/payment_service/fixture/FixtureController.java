package com.chaosproof.payment_service.fixture;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Chaos fixtures `cpu-burner`, `hungry-worker` and `flaky-payments`. Gated
 * behind chaosproof.fixtures.enabled — absent from any public build, enabled
 * only in chaos namespaces. Fixture names match the `chaos_fixture` field on
 * experiments.
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

    /**
     * flaky-payments: fail a CONTROLLED PERCENTAGE of payment calls.
     *
     * Added for counterfactual analysis (§17), because packet loss turned out to
     * be the wrong instrument for it. Measuring what a fallback is worth needs a
     * fault severity where the "without" arm degrades but survives its own abort
     * threshold — roughly 10% of REQUESTS failing. Packet loss does not give
     * that: at 10% loss TCP retransmits recovered essentially everything and
     * both arms showed ~0 failed requests, while the loss rate that does produce
     * request failures is nonlinear and unstable near the abort boundary.
     *
     * An explicit error rate is the honest instrument for the question being
     * asked. It is also visible: every activation logs at WARN and the rate is
     * readable, so a service left flaky is discoverable rather than mysterious.
     */
    private static final AtomicInteger failPercent = new AtomicInteger(0);

    public static boolean shouldFail() {
        int pct = failPercent.get();
        return pct > 0 && ThreadLocalRandom.current().nextInt(100) < pct;
    }

    @PostMapping("/fixtures/flaky")
    public String flaky(@RequestParam(defaultValue = "0") int percent) {
        int clamped = Math.max(0, Math.min(100, percent));
        failPercent.set(clamped);
        if (clamped == 0) {
            log.warn("chaos fixture flaky-payments: DISABLED");
        } else {
            log.warn("chaos fixture flaky-payments: failing {}% of payment calls", clamped);
        }
        return "flaky-payments at " + clamped + "%";
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
