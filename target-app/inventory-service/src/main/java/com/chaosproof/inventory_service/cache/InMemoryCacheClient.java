package com.chaosproof.inventory_service.cache;

import com.chaosproof.inventory_service.api.ItemDto;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * In-process cache standing in for Redis until the compose/K8s Redis lands with the
 * later phases. The chaos hook (`setAvailable(false)`) is what lets the cache-outage
 * experiments open the `cache` circuit breaker deterministically.
 */
@Component
public class InMemoryCacheClient implements CacheClient {

    private final Map<Long, ItemDto> store = new ConcurrentHashMap<>();
    private final AtomicBoolean available = new AtomicBoolean(true);

    @Override
    public Optional<ItemDto> get(long id) {
        failIfUnavailable();
        return Optional.ofNullable(store.get(id));
    }

    @Override
    public void put(ItemDto item) {
        failIfUnavailable();
        store.put(item.id(), item);
    }

    @Override
    public void setAvailable(boolean value) {
        available.set(value);
    }

    private void failIfUnavailable() {
        if (!available.get()) {
            throw new CacheUnavailableException();
        }
    }
}
