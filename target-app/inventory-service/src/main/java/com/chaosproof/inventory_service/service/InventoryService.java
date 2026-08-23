package com.chaosproof.inventory_service.service;

import com.chaosproof.inventory_service.api.ItemDto;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * Read path: cache first (CB `cache`), then DB (retry `database` + stale fallback).
 * Cache failures degrade to the DB path; DB failures degrade to stale data.
 * Both degradations are metered — that is the whole point of this target app.
 */
@Service
public class InventoryService {

    private static final Logger log = LoggerFactory.getLogger(InventoryService.class);

    private final CacheReader cache;
    private final DbReader db;

    public InventoryService(CacheReader cache, DbReader db) {
        this.cache = cache;
        this.db = db;
    }

    public ItemDto getItem(long id) {
        try {
            var hit = cache.read(id);
            if (hit.isPresent()) {
                return hit.get();
            }
        } catch (Exception e) {
            // Cache down or breaker open: fall through to the DB path.
            log.debug("cache path unavailable for item {}: {}", id, e.getClass().getSimpleName());
        }

        var item = db.read(id);
        if (!item.stale()) {
            try {
                cache.write(item);
            } catch (Exception ignored) {
                // Best-effort write-back; the read already succeeded.
            }
        }
        return item;
    }
}
