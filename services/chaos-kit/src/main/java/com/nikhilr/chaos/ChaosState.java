package com.nikhilr.chaos;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

/**
 * Current fault injection settings for this instance.
 * <p>
 * The memory leak keeps allocating and holding byte arrays, but stops at 85% of the max
 * heap so the JVM degrades visibly instead of dying before anyone can notice.
 */
public class ChaosState {

    private static final Logger log = LoggerFactory.getLogger(ChaosState.class);
    private static final int MB = 1024 * 1024;
    private static final double LEAK_CEILING = 0.85;

    private volatile int latencyMs;
    private volatile double errorRate;
    private volatile int leakMbPerSecond;
    private final List<byte[]> leaked = new ArrayList<>();
    private final ScheduledExecutorService leaker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "chaos-leaker");
        t.setDaemon(true);
        return t;
    });

    public ChaosState(ChaosProperties initial) {
        setLatencyMs(initial.latencyMs());
        setErrorRate(initial.errorRate());
        setLeakMbPerSecond(initial.leakMbPerSecond());
        leaker.scheduleAtFixedRate(this::leakTick, 1, 1, TimeUnit.SECONDS);
    }

    public int latencyMs() {
        return latencyMs;
    }

    public double errorRate() {
        return errorRate;
    }

    public int leakMbPerSecond() {
        return leakMbPerSecond;
    }

    public void setLatencyMs(int latencyMs) {
        this.latencyMs = Math.max(0, Math.min(latencyMs, 30_000));
    }

    public void setErrorRate(double errorRate) {
        this.errorRate = Math.max(0.0, Math.min(errorRate, 1.0));
    }

    public void setLeakMbPerSecond(int leakMbPerSecond) {
        this.leakMbPerSecond = Math.max(0, Math.min(leakMbPerSecond, 200));
    }

    /** Should this request fail? */
    public boolean shouldFail() {
        double rate = errorRate;
        return rate > 0 && ThreadLocalRandom.current().nextDouble() < rate;
    }

    /** Latency to add to this request, with a little jitter so it looks like real slowness. */
    public long delayMs() {
        int base = latencyMs;
        if (base <= 0) {
            return 0;
        }
        return base + ThreadLocalRandom.current().nextInt(Math.max(1, base / 5));
    }

    public synchronized long leakedMb() {
        return (long) leaked.size();
    }

    public synchronized void reset() {
        latencyMs = 0;
        errorRate = 0.0;
        leakMbPerSecond = 0;
        leaked.clear();
        log.info("Chaos reset");
    }

    synchronized void leakTick() {
        int rate = leakMbPerSecond;
        if (rate <= 0) {
            return;
        }
        Runtime rt = Runtime.getRuntime();
        for (int i = 0; i < rate; i++) {
            long used = rt.totalMemory() - rt.freeMemory();
            if ((double) (used + MB) / rt.maxMemory() >= LEAK_CEILING) {
                return;
            }
            leaked.add(new byte[MB]);
        }
    }

    public Snapshot snapshot() {
        Runtime rt = Runtime.getRuntime();
        long usedMb = (rt.totalMemory() - rt.freeMemory()) / MB;
        return new Snapshot(latencyMs, errorRate, leakMbPerSecond, leakedMb(), usedMb, rt.maxMemory() / MB);
    }

    public record Snapshot(int latencyMs, double errorRate, int leakMbPerSecond, long leakedMb,
                           long heapUsedMb, long heapMaxMb) {
    }
}
