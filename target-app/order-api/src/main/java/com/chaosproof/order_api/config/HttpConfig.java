package com.chaosproof.order_api.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;
import java.time.Duration;

/** Global 3s timeout on every downstream call — the gateway's third pattern. */
@Configuration
public class HttpConfig {

    private static final Duration TIMEOUT = Duration.ofSeconds(3);

    private RestClient client(String baseUrl) {
        var factory = new JdkClientHttpRequestFactory(
                HttpClient.newBuilder().connectTimeout(TIMEOUT).build());
        factory.setReadTimeout(TIMEOUT);
        return RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
    }

    @Bean
    RestClient paymentRestClient(@Value("${chaosproof.payment-url}") String url) {
        return client(url);
    }

    @Bean
    RestClient inventoryRestClient(@Value("${chaosproof.inventory-url}") String url) {
        return client(url);
    }
}
