package org.enveloping.ecobin.framework.observability.sql;

import net.ttddyy.dsproxy.support.ProxyDataSource;
import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.support.StaticListableBeanFactory;
import tools.jackson.databind.json.JsonMapper;

import javax.sql.DataSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class SqlSummaryDataSourceBeanPostProcessorTest {

    @Test
    void wrapsDataSourceOnlyWhenSqlDiagnosticsAreEnabled() {
        DiagnosticLoggingProperties properties =
                new DiagnosticLoggingProperties();
        SqlQuerySummaryListener listener = new SqlQuerySummaryListener(
                properties,
                new DiagnosticPayloadSanitizer(
                        JsonMapper.builder().build()));
        StaticListableBeanFactory beanFactory =
                new StaticListableBeanFactory();
        beanFactory.addBean("diagnosticLoggingProperties", properties);
        beanFactory.addBean("sqlQuerySummaryListener", listener);
        SqlSummaryDataSourceBeanPostProcessor processor =
                new SqlSummaryDataSourceBeanPostProcessor(
                        beanFactory.getBeanProvider(
                                DiagnosticLoggingProperties.class),
                        beanFactory.getBeanProvider(
                                SqlQuerySummaryListener.class));
        DataSource original = mock(DataSource.class);

        assertThat(processor.postProcessAfterInitialization(
                original, "dataSource")).isSameAs(original);

        properties.getSql().setEnabled(true);
        assertThat(processor.postProcessAfterInitialization(
                original, "dataSource"))
                .isInstanceOf(ProxyDataSource.class)
                .isNotSameAs(original);
    }
}
