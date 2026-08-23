package com.chaosproof.inventory_service.repo;

import com.chaosproof.inventory_service.domain.Item;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ItemRepository extends JpaRepository<Item, Long> {
}
