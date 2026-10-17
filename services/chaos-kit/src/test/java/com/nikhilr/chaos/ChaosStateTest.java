package com.nikhilr.chaos;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class ChaosStateTest {

    @Test
    void startsHealthyByDefault() {
        ChaosState state = new ChaosState(new ChaosProperties(0, 0.0, 0));
        assertThat(state.shouldFail()).isFalse();
        assertThat(state.delayMs()).isZero();
    }

    @Test
    void errorRateOfOneAlwaysFails() {
        ChaosState state = new ChaosState(new ChaosProperties(0, 1.0, 0));
        for (int i = 0; i < 50; i++) {
            assertThat(state.shouldFail()).isTrue();
        }
    }

    @Test
    void valuesAreClamped() {
        ChaosState state = new ChaosState(new ChaosProperties(0, 0.0, 0));
        state.setErrorRate(5);
        state.setLatencyMs(-10);
        state.setLeakMbPerSecond(10_000);
        assertThat(state.errorRate()).isEqualTo(1.0);
        assertThat(state.latencyMs()).isZero();
        assertThat(state.leakMbPerSecond()).isEqualTo(200);
    }

    @Test
    void latencyHasJitterAboveTheBase() {
        ChaosState state = new ChaosState(new ChaosProperties(500, 0.0, 0));
        long delay = state.delayMs();
        assertThat(delay).isBetween(500L, 600L);
    }

    @Test
    void leakHoldsMemoryUntilReset() {
        ChaosState state = new ChaosState(new ChaosProperties(0, 0.0, 0));
        state.setLeakMbPerSecond(4);
        state.leakTick();
        assertThat(state.leakedMb()).isPositive();
        state.reset();
        assertThat(state.leakedMb()).isZero();
        assertThat(state.leakMbPerSecond()).isZero();
    }
}
