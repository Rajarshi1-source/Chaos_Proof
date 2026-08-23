package com.chaosproof.inventory_service.api;

/** `stale=true` marks the stale-cache degradation path — observable, per the contract rule. */
public record ItemDto(Long id, String name, int quantity, boolean stale) {

    public ItemDto asStale() {
        return new ItemDto(id, name, quantity, true);
    }
}
