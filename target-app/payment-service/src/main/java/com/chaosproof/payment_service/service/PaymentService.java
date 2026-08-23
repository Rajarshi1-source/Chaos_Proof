package com.chaosproof.payment_service.service;

import io.github.resilience4j.bulkhead.annotation.Bulkhead;
import io.github.resilience4j.ratelimiter.annotation.RateLimiter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Queue;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Payment is critical: bulkhead `paymentProcessing` (25 concurrent) prevents thread
 * starvation, rate limiter `paymentApi` (100 rps) prevents overload, and the fallback
 * QUEUES for async retry instead of failing the user. The queue depth is published —
 * a graceful_degradation_mode that fires invisibly is untestable (contract rule).
 */
@Service
public class PaymentService {

    public record PaymentRequest(String orderId, long amountPaise) {}
    public record PaymentResult(String status, String reference) {}

    private static final Logger log = LoggerFactory.getLogger(PaymentService.class);
    private static final int MAX_QUEUE_DEPTH = 1000;   // pledged in the resilience contract

    private final Queue<PaymentRequest> retryQueue = new ConcurrentLinkedQueue<>();

    public PaymentService(MeterRegistry registry) {
        Gauge.builder("chaosproof.payment.queue.depth", retryQueue, Queue::size)
                .description("payments queued for async retry by the fallback")
                .register(registry);
    }

    @RateLimiter(name = "paymentApi", fallbackMethod = "queueFallback")
    @Bulkhead(name = "paymentProcessing", fallbackMethod = "queueFallback")
    public PaymentResult process(PaymentRequest request) {
        // Simulated processing: deterministic, fast, no external dependency.
        return new PaymentResult("CHARGED", "pay-" + UUID.randomUUID());
    }

    PaymentResult queueFallback(PaymentRequest request, Throwable cause) {
        if (retryQueue.size() >= MAX_QUEUE_DEPTH) {
            log.error("payment retry queue FULL ({}): rejecting {}", MAX_QUEUE_DEPTH, request.orderId());
            throw new IllegalStateException("payment queue full", cause);
        }
        retryQueue.add(request);
        log.warn("payment queued for retry ({}): {}", cause.getClass().getSimpleName(), request.orderId());
        return new PaymentResult("QUEUED", "queued:" + request.orderId());
    }
}
