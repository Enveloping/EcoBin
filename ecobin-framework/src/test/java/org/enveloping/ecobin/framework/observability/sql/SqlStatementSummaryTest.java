package org.enveloping.ecobin.framework.observability.sql;

import net.ttddyy.dsproxy.QueryInfo;
import net.ttddyy.dsproxy.proxy.ParameterSetOperation;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.sql.PreparedStatement;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class SqlStatementSummaryTest {

    @Test
    void removesInlineValuesAndReportsOnlyParameterTypes() throws Exception {
        QueryInfo query = new QueryInfo("""
                SELECT user.id, wallet.balance_cent
                FROM iam_organization_user user
                JOIN fund_user_wallet wallet ON wallet.organization_user_id = user.id
                WHERE user.phone = '13900000000'
                  AND user.id = 42
                  AND user.secret_digest = 0xDEADBEEF
                  AND user.openid = ?
                """);
        Method setString = PreparedStatement.class.getMethod(
                "setString", int.class, String.class);
        query.setParametersList(List.of(List.of(
                new ParameterSetOperation(
                        setString,
                        new Object[]{1, "sensitive-openid"}))));

        SqlStatementSummary.Summary summary =
                SqlStatementSummary.from(
                        List.of(query), 4_096, true);

        assertThat(summary.operation()).isEqualTo("SELECT");
        assertThat(summary.tables())
                .contains("iam_organization_user")
                .contains("fund_user_wallet");
        assertThat(summary.parameterCount()).isEqualTo(1);
        assertThat(summary.parameterTypes()).isEqualTo("String");
        assertThat(summary.sql())
                .doesNotContain("13900000000")
                .doesNotContain("sensitive-openid")
                .doesNotContain("42")
                .doesNotContain("DEADBEEF")
                .contains("user.phone = ?")
                .contains("user.id = ?");
    }

    @Test
    void truncatesLargeSqlAfterRemovingComments() {
        QueryInfo query = new QueryInfo(
                "/* password=do-not-log */ SELECT * FROM dev_device_asset "
                        + "WHERE hardware_sn = '" + "x".repeat(1_000) + "'");

        SqlStatementSummary.Summary summary =
                SqlStatementSummary.from(List.of(query), 256, false);

        assertThat(summary.sql())
                .doesNotContain("do-not-log")
                .doesNotContain("x".repeat(100))
                .contains("SELECT * FROM dev_device_asset")
                .hasSizeLessThan(320);
    }
}
