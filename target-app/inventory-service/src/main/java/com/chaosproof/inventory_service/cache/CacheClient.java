package com.chaosproof.inventory_service.cache;

import com.chaosproof.inventory_service.api.ItemDto;

import java.util.Optional;

/** Cache abstraction the `cache` circuit breaker wraps. */
public interface CacheClient {

    Optional<ItemDto> get(long id);

    void put(ItemDto item);

    /** Chaos hook: while unavailable, every get/put throws CacheUnavailableException. */
    void setAvailable(boolean available);

    class CacheUnavailableException extends RuntimeException {
        public CacheUnavailableException() {
            super("cache unavailable (chaos fixture)");
        }
    }
}
