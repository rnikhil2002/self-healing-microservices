package com.nikhilr.orders;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
class OrderServiceTest {

    private static final String ORDER = """
            {"customerId": "cust-42", "items": [
              {"sku": "SKU-1", "quantity": 2, "price": 19.99},
              {"sku": "SKU-2", "quantity": 1, "price": 5.00}
            ]}
            """;

    @Autowired
    MockMvc mvc;

    @MockBean
    PaymentClient payments;

    @Test
    void confirmsOrderWhenPaymentIsApproved() throws Exception {
        when(payments.authorize(anyString(), anyString(), any())).thenReturn(Optional.of("APPROVED"));

        mvc.perform(post("/api/orders").contentType(MediaType.APPLICATION_JSON).content(ORDER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CONFIRMED"))
                .andExpect(jsonPath("$.total").value(44.98));
    }

    @Test
    void degradesToPendingWhenPaymentServiceIsDown() throws Exception {
        when(payments.authorize(anyString(), anyString(), any())).thenReturn(Optional.empty());

        mvc.perform(post("/api/orders").contentType(MediaType.APPLICATION_JSON).content(ORDER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("PAYMENT_PENDING"));
    }

    @Test
    void rejectsEmptyOrders() throws Exception {
        mvc.perform(post("/api/orders").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"customerId\": \"c\", \"items\": []}"))
                .andExpect(status().isBadRequest());
    }
}
