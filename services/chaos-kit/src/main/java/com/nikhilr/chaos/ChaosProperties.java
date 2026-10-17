package com.nikhilr.chaos;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Starting fault settings. Setting them through environment variables (CHAOS_ERROR_RATE,
 * CHAOS_LATENCY_MS, CHAOS_LEAK_MB_PER_SECOND) is how a "bad release" is simulated.
 */
@ConfigurationProperties(prefix = "chaos")
public record ChaosProperties(int latencyMs, double errorRate, int leakMbPerSecond) {
}
