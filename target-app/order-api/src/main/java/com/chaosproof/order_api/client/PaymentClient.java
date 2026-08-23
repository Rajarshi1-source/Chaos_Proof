package com.chaosproof.order_api.client;

import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** Circuit breaker `paymentService` — the single most important pattern ChaosProof validates. */
@Component
public class PaymentClient {

    public record PaymentRequest(String orderId, long amountPaise) {}
    public record PaymentResult(String status, String reference) {}

    private final RestClient rest;

    public PaymentClient(@Qualifier("paymentRestClient") RestClient rest) {
        this.rest = rest;
    }

    @CircuitBreaker(name = "paymentService", fallbackMethod = "queuedFallback")
    public PaymentResult charge(PaymentRequest request) {
        return rest.post().uri("/api/payments").body(request).retrieve().body(PaymentResult.class);
    }

    /** Graceful degradation: payment is queued for async processing, user gets a working response. */
    PaymentResult queuedFallback(PaymentRequest request, Throwable cause) {
        return new PaymentResult("QUEUED", "cb-fallback:" + request.orderId());
    }
}
