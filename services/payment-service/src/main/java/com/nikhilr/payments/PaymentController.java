package com.nikhilr.payments;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.Map;
import java.util.UUID;

/** A small payment authorizer: approves anything under the per-order limit. */
@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private static final BigDecimal LIMIT = new BigDecimal("5000");

    private final String version;

    public PaymentController(@Value("${app.version:1.0.0}") String version) {
        this.version = version;
    }

    @PostMapping("/authorize")
    public AuthorizationResponse authorize(@Valid @RequestBody AuthorizationRequest request) {
        boolean approved = request.amount().compareTo(LIMIT) <= 0;
        return new AuthorizationResponse(
                "pay-" + UUID.randomUUID().toString().substring(0, 12),
                request.orderId(),
                approved ? "APPROVED" : "DECLINED",
                approved ? null : "amount over limit");
    }

    @GetMapping("/info")
    public Map<String, String> info() {
        return Map.of("service", "payment-service", "version", version);
    }

    public record AuthorizationRequest(@NotBlank String orderId, @NotBlank String customerId,
                                       @Positive BigDecimal amount) {
    }

    public record AuthorizationResponse(String paymentId, String orderId, String status, String reason) {
    }
}
