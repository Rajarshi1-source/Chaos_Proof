package com.chaosproof.order_api.config;

import io.micrometer.core.instrument.Meter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.config.MeterFilter;
import io.micrometer.core.instrument.distribution.DistributionStatisticConfig;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Rename-proof metric guarantees, independent of Boot property names:
 * the `application` tag every ChaosProof query filters on, and histogram
 * buckets for http.server.requests so histogram_quantile() has _bucket series.
 */
@Configuration
public class MetricsConfig {

    @Bean
    MeterFilter httpServerHistogramFilter() {
        return new MeterFilter() {
            @Override
            public DistributionStatisticConfig configure(Meter.Id id, DistributionStatisticConfig config) {
                if (id.getName().equals("http.server.requests")) {
                    return DistributionStatisticConfig.builder()
                            .percentilesHistogram(true)
                            .build()
                            .merge(config);
                }
                return config;
            }
        };
    }

    @Bean
    static org.springframework.beans.factory.config.BeanPostProcessor commonTagsPostProcessor(
            org.springframework.core.env.Environment env) {
        return new org.springframework.beans.factory.config.BeanPostProcessor() {
            @Override
            public Object postProcessAfterInitialization(Object bean, String name) {
                if (bean instanceof MeterRegistry registry) {
                    registry.config().commonTags("application",
                            env.getProperty("spring.application.name", "unknown"));
                }
                return bean;
            }
        };
    }
}
