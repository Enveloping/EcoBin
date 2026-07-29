package org.enveloping.ecobin.identity.application.startdelivery;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JdbcStartDeliveryIdentityLockRepositoryTest {

    @Test
    void everyIdentityNodeUsesAnIndependentForUpdateStatement() {
        List<String> statements = List.of(
                JdbcStartDeliveryIdentityLockRepository.LOCK_TENANT_SQL,
                JdbcStartDeliveryIdentityLockRepository
                        .LOCK_ORGANIZATION_SQL,
                JdbcStartDeliveryIdentityLockRepository.LOCK_MINIAPP_SQL,
                JdbcStartDeliveryIdentityLockRepository
                        .LOCK_ORGANIZATION_USER_SQL,
                JdbcStartDeliveryIdentityLockRepository
                        .LOCK_ORGANIZATION_USER_SESSION_SQL);
        List<String> expectedTables = List.of(
                "iam_tenant",
                "iam_organization",
                "iam_organization_miniapp",
                "iam_organization_user",
                "iam_organization_user_session");

        for (int index = 0; index < statements.size(); index++) {
            String sql = normalize(statements.get(index));
            assertTrue(sql.contains(
                    "from " + expectedTables.get(index)));
            assertTrue(sql.endsWith("for update"));
            assertFalse(sql.contains(" join "));
        }
    }

    private static String normalize(String sql) {
        return sql.toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ")
                .trim();
    }
}
