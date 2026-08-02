package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.EnableTransactionManagement;

import javax.sql.DataSource;

import static org.junit.jupiter.api.Assertions.assertTrue;

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_H02_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class ReliableDeviceCommandLeastPrivilegeMysqlIntegrationTest {

    private AnnotationConfigApplicationContext context;
    private ReliableTaskClaimService claimService;

    @BeforeAll
    void startContext() {
        context = new AnnotationConfigApplicationContext(TestConfiguration.class);
        claimService = context.getBean(ReliableTaskClaimService.class);
    }

    @AfterAll
    void closeContext() {
        if (context != null) {
            context.close();
        }
    }

    @Test
    void runtimePrincipalCanEvaluateAnEmptyDeviceCommandQueue() {
        assertTrue(claimService.claimNextDeviceCommand(
                "h02-least-privilege-probe").isEmpty());
    }

    @Configuration(proxyBeanMethods = false)
    @EnableTransactionManagement
    static class TestConfiguration {

        @Bean
        DataSource dataSource() {
            return new DriverManagerDataSource(
                    requiredEnvironment("ECOBIN_H02_MYSQL_URL"),
                    requiredEnvironment("ECOBIN_H02_MYSQL_USERNAME"),
                    requiredEnvironment("ECOBIN_H02_MYSQL_PASSWORD"));
        }

        @Bean
        JdbcTemplate jdbcTemplate(DataSource dataSource) {
            return new JdbcTemplate(dataSource);
        }

        @Bean
        PlatformTransactionManager transactionManager(DataSource dataSource) {
            return new DataSourceTransactionManager(dataSource);
        }

        @Bean
        ReliableTaskProperties reliableTaskProperties() {
            return new ReliableTaskProperties();
        }

        @Bean
        ReliableOperationsJdbcRepository repository(JdbcTemplate jdbcTemplate) {
            return new ReliableOperationsJdbcRepository(jdbcTemplate);
        }

        @Bean
        ReliableTaskClaimService claimService(
                ReliableOperationsJdbcRepository repository,
                ReliableTaskProperties properties) {
            return new ReliableTaskClaimService(repository, properties);
        }

        private static String requiredEnvironment(String name) {
            String value = System.getenv(name);
            if (value == null || value.isBlank()) {
                throw new IllegalStateException(name + " must be configured");
            }
            return value;
        }
    }
}
