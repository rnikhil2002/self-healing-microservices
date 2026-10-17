package com.nikhilr.orders;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.math.BigDecimal;
import java.util.Map;
import java.util.Optional;

/** Calls payment-service. Returns empty when it is failing so orders can degrade gracefully. */
@Component
public class PaymentClient {

    private final RestClient client;

    public PaymentClient(@Value("${payment-service.url:http://localhost:8082}") String baseUrl) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(2000);
        factory.setReadTimeout(5000);
        this.client = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
    }

    public Optional<String> authorize(String orderId, String customerId, BigDecimal amount) {
        try {
            AuthorizationResponse response = client.post()
                    .uri("/api/payments/authorize")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("orderId", orderId, "customerId", customerId, "amount", amount))
                    .retrieve()
                    .body(AuthorizationResponse.class);
            return Optional.ofNullable(response).map(AuthorizationResponse::status);
        } catch (RestClientException ex) {
            return Optional.empty();
        }
    }

    record AuthorizationResponse(String paymentId, String orderId, String status, String reason) {
    }
}
