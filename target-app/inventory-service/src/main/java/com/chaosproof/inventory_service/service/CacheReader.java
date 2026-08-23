package com.chaosproof.inventory_service.service;

import com.chaosproof.inventory_service.api.ItemDto;
import com.chaosproof.inventory_service.cache.CacheClient;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * Circuit breaker `cache` around the cache client. A SEPARATE bean from the service
 * that calls it: Resilience4j annotations are proxy-based and self-invocation
 * silently bypasses them — the failure mode that looks like a pattern not activating.
 */
@Component
public class CacheReader {

    private final CacheClient cache;

    public CacheReader(CacheClient cache) {
        this.cache = cache;
    }

    @CircuitBreaker(name = "cache")
    public Optional<ItemDto> read(long id) {
        return cache.get(id);
    }

    @CircuitBreaker(name = "cache")
    public void write(ItemDto item) {
        cache.put(item);
    }
}
