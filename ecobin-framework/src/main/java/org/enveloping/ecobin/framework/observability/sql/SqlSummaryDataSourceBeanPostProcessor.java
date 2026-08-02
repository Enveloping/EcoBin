package org.enveloping.ecobin.framework.observability.sql;

import net.ttddyy.dsproxy.support.ProxyDataSource;
import net.ttddyy.dsproxy.support.ProxyDataSourceBuilder;
import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;

/** local-real 开启时，为 DataSource 增加只读 SQL 执行观察器。 */
@Component
public final class SqlSummaryDataSourceBeanPostProcessor
        implements BeanPostProcessor {

    private final ObjectProvider<DiagnosticLoggingProperties>
            propertiesProvider;
    private final ObjectProvider<SqlQuerySummaryListener> listenerProvider;

    public SqlSummaryDataSourceBeanPostProcessor(
            ObjectProvider<DiagnosticLoggingProperties> propertiesProvider,
            ObjectProvider<SqlQuerySummaryListener> listenerProvider) {
        this.propertiesProvider = propertiesProvider;
        this.listenerProvider = listenerProvider;
    }

    @Override
    public Object postProcessAfterInitialization(
            Object bean,
            String beanName) throws BeansException {
        if (!(bean instanceof DataSource dataSource)
                || bean instanceof ProxyDataSource) {
            return bean;
        }
        DiagnosticLoggingProperties properties =
                propertiesProvider.getIfAvailable();
        if (properties == null
                || !properties.getSql().isEnabled()) {
            return bean;
        }
        SqlQuerySummaryListener listener =
                listenerProvider.getIfAvailable();
        if (listener == null) {
            return bean;
        }
        return ProxyDataSourceBuilder
                .create("ecobin", dataSource)
                .listener(listener)
                .build();
    }
}
