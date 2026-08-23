package com.chaosproof.payment_service.api;

import com.chaosproof.payment_service.service.PaymentService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PaymentController {

    private final PaymentService payments;

    public PaymentController(PaymentService payments) {
        this.payments = payments;
    }

    @PostMapping("/api/payments")
    public PaymentService.PaymentResult pay(@RequestBody PaymentService.PaymentRequest request) {
        return payments.process(request);
    }
}
