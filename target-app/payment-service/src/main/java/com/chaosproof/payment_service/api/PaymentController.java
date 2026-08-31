package com.chaosproof.payment_service.api;

import com.chaosproof.payment_service.fixture.FixtureController;
import com.chaosproof.payment_service.service.PaymentService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class PaymentController {

    private final PaymentService payments;

    public PaymentController(PaymentService payments) {
        this.payments = payments;
    }

    @PostMapping("/api/payments")
    public PaymentService.PaymentResult pay(@RequestBody PaymentService.PaymentRequest request) {
        // The `flaky-payments` fixture, checked before any real work. Inert
        // unless a counterfactual run has armed it (default 0%), and the
        // fixture class itself only exists when chaosproof.fixtures.enabled is
        // set — so this is absent from any public build.
        if (FixtureController.shouldFail()) {
            throw new ResponseStatusException(
                    HttpStatus.INTERNAL_SERVER_ERROR, "chaos fixture flaky-payments");
        }
        return payments.process(request);
    }
}
