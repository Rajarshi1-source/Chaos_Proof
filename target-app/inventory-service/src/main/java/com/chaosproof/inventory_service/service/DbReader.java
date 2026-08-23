package com.chaosproof.inventory_service.service;

import com.chaosproof.inventory_service.api.ItemDto;
import com.chaosproof.inventory_service.repo.TimedInventoryRepository;
import io.github.resilience4j.retry.annotation.Retry;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Retry `database` around the timed repository, with the stale-cache fallback:
 * when the DB is down beyond the retry budget, serve the last-known-good value
 * (marked stale) rather than failing the read. The fallback firing is observable
 * via chaosproof_inventory_stale_served_total — an invisible degradation mode is
 * untestable (contract rule).
 */
@Component
public class DbReader {

    private static final Logger log = LoggerFactory.getLogger(DbReader.class);

    private final TimedInventoryRepository repository;
    private final Map<Long, ItemDto> lastKnownGood = new ConcurrentHashMap<>();
    private final Counter staleServed;

    public DbReader(TimedInventoryRepository repository, MeterRegistry registry) {
        this.repository = repository;
        this.staleServed = Counter.builder("chaosproof.inventory.stale.served")
                .description("reads served from the stale store because DB retries were exhausted")
                .register(registry);
    }

    @Retry(name = "database", fallbackMethod = "staleFallback")
    public ItemDto read(long id) {
        var item = repository.findById(id)
                .map(i -> new ItemDto(i.getId(), i.getName(), i.getQuantity(), false))
                .orElseThrow(() -> new ItemNotFoundException(id));
        lastKnownGood.put(id, item);
        return item;
    }

    ItemDto staleFallback(long id, Throwable cause) {
        if (cause instanceof ItemNotFoundException e) {
            throw e;   // a genuine 404 is not a degradation — never mask it as stale data
        }
        var stale = lastKnownGood.get(id);
        if (stale == null) {
            log.error("DB unavailable and no stale value for item {}", id, cause);
            throw new IllegalStateException("inventory unavailable, no stale fallback for " + id, cause);
        }
        staleServed.increment();
        log.warn("serving STALE inventory for item {} ({})", id, cause.getClass().getSimpleName());
        return stale.asStale();
    }

    public static class ItemNotFoundException extends RuntimeException {
        ItemNotFoundException(long id) {
            super("item " + id + " not found");
        }
    }
}
