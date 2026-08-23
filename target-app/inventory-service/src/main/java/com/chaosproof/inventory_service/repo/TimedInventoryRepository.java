package com.chaosproof.inventory_service.repo;

import com.chaosproof.inventory_service.domain.Item;
import io.micrometer.core.annotation.Timed;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * D-I: the §18 cascade's conditional trigger reads `db_query_seconds_bucket`, which
 * Spring's default JDBC/Hikari metrics do NOT emit. This wrapper creates it.
 * @Timed is proxy-based — callers must be OTHER beans, never this one.
 */
@Component
public class TimedInventoryRepository {

    private final ItemRepository items;

    public TimedInventoryRepository(ItemRepository items) {
        this.items = items;
    }

    @Timed(value = "db_query", extraTags = {"repository", "inventory"}, histogram = true)
    public Optional<Item> findById(Long id) {
        return items.findById(id);
    }
}
