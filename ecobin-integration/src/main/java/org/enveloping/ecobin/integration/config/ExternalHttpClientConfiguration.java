package org.enveloping.ecobin.integration.config;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

/**
 * 真实外联适配器共用的有界 HTTP 客户端。
 */
@Configuration
public class ExternalHttpClientConfiguration {

    @Bean
    @ConditionalOnProperty(
            prefix = "ecobin.external",
            name = "mode",
            havingValue = "real")
    public RestTemplate restTemplate() {
        SimpleClientHttpRequestFactory factory =
                new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(5000);
        factory.setReadTimeout(5000);
        return new RestTemplate(factory);
    }
}
