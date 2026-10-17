package com.nikhilr.orders;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Positive;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private static final int MAX_ORDERS = 10_000;

    private final PaymentClient payments;
    private final String version;
    private final Map<String, Order> orders = new ConcurrentHashMap<>();

    public OrderController(PaymentClient payments, @Value("${app.version:1.0.0}") String version) {
        this.payments = payments;
        this.version = version;
    }

    @PostMapping
    public Order create(@Valid @RequestBody OrderRequest request) {
        String id = "ord-" + UUID.randomUUID().toString().substring(0, 12);
        BigDecimal total = request.items().stream()
                .map(i -> i.price().multiply(BigDecimal.valueOf(i.quantity())))
                .reduce(BigDecimal.ZERO, BigDecimal::add);

        // If payment-service is down the order is still accepted and retried later.
        String status = payments.authorize(id, request.customerId(), total)
                .map(s -> s.equals("APPROVED") ? "CONFIRMED" : "PAYMENT_DECLINED")
                .orElse("PAYMENT_PENDING");

        Order order = new Order(id, request.customerId(), request.items(), total, status, Instant.now());
        if (orders.size() >= MAX_ORDERS) {
            orders.clear();
        }
        orders.put(id, order);
        return order;
    }

    @GetMapping("/{id}")
    public ResponseEntity<Order> get(@PathVariable String id) {
        Order order = orders.get(id);
        return order == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(order);
    }

    @GetMapping("/info")
    public Map<String, Object> info() {
        return Map.of("service", "order-service", "version", version, "orders", orders.size());
    }

    public record OrderRequest(@NotBlank String customerId, @NotEmpty List<@Valid Item> items) {
    }

    public record Item(@NotBlank String sku, @Positive int quantity, @Positive BigDecimal price) {
    }

    public record Order(String id, String customerId, List<Item> items, BigDecimal total, String status,
                        Instant createdAt) {
    }
}
