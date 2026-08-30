package com.chaosproof.order_api.client;

import com.chaosproof.order_api.resilience.ResilienceFlags;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** Circuit breaker `paymentService` — the single most important pattern ChaosProof validates. */
@Component
public class PaymentClient {

    public record PaymentRequest(String orderId, long amountPaise) {}
    public record PaymentResult(String status, String reference) {}

    /** The pattern name the counterfactual flag plane toggles. */
    public static final String FALLBACK_PATTERN = "paymentService.fallback";

    private final RestClient rest;
    private final ResilienceFlags flags;

    public PaymentClient(@Qualifier("paymentRestClient") RestClient rest, ResilienceFlags flags) {
        this.rest = rest;
        this.flags = flags;
    }

    // GATE 7: @CircuitBreaker deliberately removed. This is the change the CI
    // chaos gate exists to refuse.
    //
    // The graceful degradation is kept, by hand, so that ONLY the breaker is
    // gone. Deleting the annotation outright also deletes `fallbackMethod`, and
    // the resulting build fails so hard that pre-flight refuses to inject into
    // it - a correct gate failure, but one that never reaches the hypothesis.
    // Keeping the catch isolates the exact property the experiment asserts:
    // the user still gets a response, and nothing opens a circuit.
    public PaymentResult charge(PaymentRequest request) {
        try {
            return rest.post().uri("/api/payments").body(request).retrieve().body(PaymentResult.class);
        } catch (RuntimeException cause) {
            return queuedFallback(request, cause);
        }
    }

    /**
     * Graceful degradation: payment is queued for async processing, user gets a
     * working response.
     *
     * The counterfactual "without" arm disables this so the fault reaches the user —
     * that is the whole point of measuring what a pattern is worth. It is also what
     * lets the safety plane's abort probes be exercised for real: with the fallback
     * on, a total partition still yields 100% client availability, so nothing would
     * ever cross an abort threshold.
     */
    PaymentResult queuedFallback(PaymentRequest request, Throwable cause) {
        if (flags.isDisabled(FALLBACK_PATTERN)) {
            throw new FallbackDisabledException(FALLBACK_PATTERN, cause);
        }
        return new PaymentResult("QUEUED", "cb-fallback:" + request.orderId());
    }

    /** Deliberate degradation, never an accident — named so it reads clearly in a trace. */
    public static class FallbackDisabledException extends RuntimeException {
        public FallbackDisabledException(String pattern, Throwable cause) {
            super("resilience pattern '" + pattern + "' is deliberately disabled "
                  + "(counterfactual arm); the underlying failure was: " + cause, cause);
        }
    }
}
