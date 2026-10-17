package com.nikhilr.chaos;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.core.Ordered;

/** Import this from a service to get fault injection: {@code @Import(ChaosConfiguration.class)}. */
@Configuration
@EnableConfigurationProperties(ChaosProperties.class)
@Import(ChaosController.class)
public class ChaosConfiguration {

    @Bean
    public ChaosState chaosState(ChaosProperties properties) {
        return new ChaosState(properties);
    }

    @Bean
    public FilterRegistrationBean<ChaosFilter> chaosFilter(ChaosState state) {
        FilterRegistrationBean<ChaosFilter> registration = new FilterRegistrationBean<>(new ChaosFilter(state));
        registration.addUrlPatterns("/*");
        // Run after the metrics filter so injected failures and latency are measured.
        registration.setOrder(Ordered.LOWEST_PRECEDENCE - 10);
        return registration;
    }
}
