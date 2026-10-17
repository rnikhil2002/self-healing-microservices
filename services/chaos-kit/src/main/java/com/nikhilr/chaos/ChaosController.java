package com.nikhilr.chaos;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Endpoints to break the service on purpose and to put it back. */
@RestController
@RequestMapping("/chaos")
public class ChaosController {

    private final ChaosState state;

    public ChaosController(ChaosState state) {
        this.state = state;
    }

    @GetMapping
    public ChaosState.Snapshot status() {
        return state.snapshot();
    }

    @PostMapping("/latency")
    public ChaosState.Snapshot latency(@RequestParam int ms) {
        state.setLatencyMs(ms);
        return state.snapshot();
    }

    @PostMapping("/errors")
    public ChaosState.Snapshot errors(@RequestParam double rate) {
        state.setErrorRate(rate);
        return state.snapshot();
    }

    @PostMapping("/memory-leak")
    public ChaosState.Snapshot memoryLeak(@RequestParam(defaultValue = "8") int mbPerSecond) {
        state.setLeakMbPerSecond(mbPerSecond);
        return state.snapshot();
    }

    @PostMapping("/reset")
    public ChaosState.Snapshot reset() {
        state.reset();
        return state.snapshot();
    }
}
