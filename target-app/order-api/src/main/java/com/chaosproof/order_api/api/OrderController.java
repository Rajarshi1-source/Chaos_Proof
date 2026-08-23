package com.chaosproof.order_api.api;

import com.chaosproof.order_api.client.InventoryClient;
import com.chaosproof.order_api.client.PaymentClient;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
public class OrderController {

    public record CheckoutResult(String orderId, String paymentStatus, String paymentRef,
                                 String item, int stock, boolean degraded) {}

    private final PaymentClient payments;
    private final InventoryClient inventory;

    public OrderController(PaymentClient payments, InventoryClient inventory) {
        this.payments = payments;
        this.inventory = inventory;
    }

    /** GET + POST: the k6 open-model profile drives this endpoint with plain GETs. */
    @RequestMapping(value = "/api/orders/checkout", method = {RequestMethod.GET, RequestMethod.POST})
    public CheckoutResult checkout(@RequestParam(defaultValue = "1") long itemId) {
        var orderId = UUID.randomUUID().toString();
        var item = inventory.check(itemId);
        var payment = payments.charge(new PaymentClient.PaymentRequest(orderId, 45_000));
        boolean degraded = item.stale() || "QUEUED".equals(payment.status());
        return new CheckoutResult(orderId, payment.status(), payment.reference(),
                item.name(), item.quantity(), degraded);
    }
}
