package com.nikhilr.payments;

import com.nikhilr.chaos.ChaosState;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
class PaymentServiceTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    ChaosState chaos;

    @AfterEach
    void reset() {
        chaos.reset();
    }

    private static String request(String amount) {
        return """
                {"orderId": "ord-1", "customerId": "cust-1", "amount": %s}
                """.formatted(amount);
    }

    @Test
    void approvesNormalAmounts() throws Exception {
        mvc.perform(post("/api/payments/authorize").contentType(MediaType.APPLICATION_JSON).content(request("120.50")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("APPROVED"));
    }

    @Test
    void declinesOverLimit() throws Exception {
        mvc.perform(post("/api/payments/authorize").contentType(MediaType.APPLICATION_JSON).content(request("9000")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("DECLINED"));
    }

    @Test
    void injectedErrorsReturn500ButHealthStaysUp() throws Exception {
        mvc.perform(post("/chaos/errors").param("rate", "1.0")).andExpect(status().isOk());

        mvc.perform(post("/api/payments/authorize").contentType(MediaType.APPLICATION_JSON).content(request("10")))
                .andExpect(status().isInternalServerError());
        mvc.perform(get("/actuator/health")).andExpect(status().isOk());
    }

    @Test
    void resetRestoresNormalBehaviour() throws Exception {
        mvc.perform(post("/chaos/errors").param("rate", "1.0"));
        mvc.perform(post("/chaos/reset")).andExpect(jsonPath("$.errorRate").value(0.0));

        mvc.perform(post("/api/payments/authorize").contentType(MediaType.APPLICATION_JSON).content(request("10")))
                .andExpect(status().isOk());
    }

    @Test
    void exposesPrometheusMetrics() throws Exception {
        mvc.perform(post("/api/payments/authorize").contentType(MediaType.APPLICATION_JSON).content(request("10")));
        mvc.perform(get("/actuator/prometheus"))
                .andExpect(status().isOk());
    }
}
