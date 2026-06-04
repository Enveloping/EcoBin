package org.enveloping.ecobin.framework.tenant;

import com.baomidou.mybatisplus.extension.plugins.handler.TenantLineHandler;
import net.sf.jsqlparser.expression.Expression;
import net.sf.jsqlparser.expression.LongValue;
import org.springframework.stereotype.Component;

import java.util.Set;

/**
 * 租户行级隔离处理器：从 {@link TenantContextHolder} 取当前租户，自动在 SQL 注入 {@code tenant_id} 条件
 * 并在 INSERT 时回填。
 * <p>
 * 以下情况放行（不注入）：
 * <ul>
 *   <li>上下文标记忽略（平台域超管/管理员、登录前流程）</li>
 *   <li>当前无租户上下文</li>
 *   <li>平台级表（自身无 tenant_id 列）：{@code sys_admin}、{@code sys_tenant}</li>
 * </ul>
 */
@Component
public class EcoBinTenantLineHandler implements TenantLineHandler {

    /** 无 tenant_id 列的平台级表，始终放行 */
    private static final Set<String> IGNORED_TABLES = Set.of("sys_admin", "sys_tenant");

    @Override
    public Expression getTenantId() {
        return new LongValue(TenantContextHolder.getTenantId());
    }

    @Override
    public String getTenantIdColumn() {
        return "tenant_id";
    }

    @Override
    public boolean ignoreTable(String tableName) {
        if (TenantContextHolder.isIgnore() || TenantContextHolder.getTenantId() == null) {
            return true;
        }
        return IGNORED_TABLES.contains(tableName.toLowerCase());
    }
}
