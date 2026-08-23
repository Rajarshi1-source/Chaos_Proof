package com.chaosproof.inventory_service.seed;

import com.chaosproof.inventory_service.domain.Item;
import com.chaosproof.inventory_service.repo.ItemRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.util.List;

/** Seeds a small catalogue so /api/inventory/{1..5} returns real rows from the first request. */
@Component
@Order(1)   // before the metrics assertion runner
public class DataSeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DataSeeder.class);

    private final ItemRepository items;

    public DataSeeder(ItemRepository items) {
        this.items = items;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (items.count() > 0) {
            return;
        }
        items.saveAll(List.of(
                new Item(1L, "mechanical keyboard", 120),
                new Item(2L, "27in monitor", 45),
                new Item(3L, "usb-c dock", 200),
                new Item(4L, "laptop stand", 310),
                new Item(5L, "webcam", 75)));
        log.info("seeded 5 catalogue items");
    }
}
