package com.chaosproof.inventory_service.api;

import com.chaosproof.inventory_service.service.DbReader;
import com.chaosproof.inventory_service.service.InventoryService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class InventoryController {

    private final InventoryService inventory;

    public InventoryController(InventoryService inventory) {
        this.inventory = inventory;
    }

    @GetMapping("/api/inventory/{id}")
    public ItemDto get(@PathVariable long id) {
        return inventory.getItem(id);
    }

    @ExceptionHandler(DbReader.ItemNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public String notFound(DbReader.ItemNotFoundException e) {
        return e.getMessage();
    }
}
