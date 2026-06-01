package org.enveloping.ecobin.framework.interceptor;

import lombok.extern.slf4j.Slf4j;
import org.apache.ibatis.executor.statement.StatementHandler;
import org.apache.ibatis.mapping.BoundSql;
import org.apache.ibatis.mapping.MappedStatement;
import org.apache.ibatis.plugin.*;
import org.apache.ibatis.reflection.DefaultReflectorFactory;
import org.apache.ibatis.reflection.MetaObject;
import org.apache.ibatis.reflection.SystemMetaObject;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;

import java.sql.Connection;
import java.util.Properties;

/**
 * MyBatis 租户拦截器（预留）
 * <p>
 * 当前单租户阶段暂不启用 SQL 改写，仅记录日志。
 * 多租户阶段通过此拦截器自动在 SQL WHERE 中注入 tenant_id 条件。
 * <p>
 * 启用方式：在 application.yml 中配置 mybatis.configuration.interceptors 或添加 @Component。
 */
@Slf4j
@Intercepts({
        @Signature(type = StatementHandler.class, method = "prepare", args = {Connection.class, Integer.class})
})
public class TenantInterceptor implements Interceptor {

    @Override
    public Object intercept(Invocation invocation) throws Throwable {
        StatementHandler statementHandler = (StatementHandler) invocation.getTarget();
        MetaObject metaObject = MetaObject.forObject(statementHandler,
                SystemMetaObject.DEFAULT_OBJECT_FACTORY,
                SystemMetaObject.DEFAULT_OBJECT_WRAPPER_FACTORY,
                new DefaultReflectorFactory());

        MappedStatement mappedStatement = (MappedStatement) metaObject.getValue("delegate.mappedStatement");
        String sqlId = mappedStatement.getId();

        // 单租户阶段仅记录 SQL 执行日志，不做 SQL 改写
        BoundSql boundSql = statementHandler.getBoundSql();
        log.debug("SQL [{}] -> {}", sqlId, boundSql.getSql().replaceAll("\\s+", " "));

        return invocation.proceed();
    }

    @Override
    public Object plugin(Object target) {
        return Plugin.wrap(target, this);
    }

    @Override
    public void setProperties(Properties properties) {
    }
}
