package com.chaosproof.order_api.client;

import io.github.resilience4j.retry.annotation.Retry;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Retry `inventoryService`, maxAttempts 3. Amplification is DELIBERATE and preserved:
 * 3 attempts against a 5% upstream error rate yields ~14% effective load — the
 * amplified rate is what the resilience contract must declare (Phase 9).
 */
@Component
public class InventoryClient {

    public record ItemDto(Long id, String name, int quantity, boolean stale) {}

    private final RestClient rest;

    public InventoryClient(@Qualifier("inventoryRestClient") RestClient rest) {
        this.rest = rest;
    }

    @Retry(name = "inventoryService", fallbackMethod = "unknownStockFallback")
    public ItemDto check(long itemId) {
        return rest.get().uri("/api/inventory/{id}", itemId).retrieve().body(ItemDto.class);
    }

    /** Degraded but working: order proceeds with unverified stock rather than failing the user. */
    ItemDto unknownStockFallback(long itemId, Throwable cause) {
        return new ItemDto(itemId, "unknown", -1, true);
    }
}
