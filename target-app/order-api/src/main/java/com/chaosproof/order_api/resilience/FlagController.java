package com.chaosproof.order_api.resilience;

import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Set;

/**
 * Runtime control of the flag plane. `@Profile("chaos-staging")` means this
 * controller DOES NOT EXIST outside that profile — not disabled, absent. A route
 * that 404s invites someone to find out why; one that was never registered does
 * not exist to be found.
 *
 * Never expose this to the internet. It is a switch that removes production
 * protection.
 */
@RestController
@Profile("chaos-staging")
public class FlagController {

    private final ResilienceFlags flags;

    public FlagController(ResilienceFlags flags) {
        this.flags = flags;
    }

    @GetMapping("/chaos/flags")
    public ResilienceFlags.Snapshot get() {
        return flags.snapshot();
    }

    /** POST /chaos/flags?enabled=true&disabled=paymentService.fallback */
    @PostMapping("/chaos/flags")
    public ResilienceFlags.Snapshot set(@RequestParam boolean enabled,
                                        @RequestParam(defaultValue = "") Set<String> disabled) {
        flags.setEnabled(enabled);
        flags.setDisabledPatterns(disabled);
        return flags.snapshot();
    }
}
