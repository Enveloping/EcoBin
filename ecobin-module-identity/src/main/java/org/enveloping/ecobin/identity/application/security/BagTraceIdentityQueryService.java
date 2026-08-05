package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.identity.api.persistence.BagTraceIdentityBatchRef;
import org.enveloping.ecobin.identity.api.port.BagTraceIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.BagTraceIdentityFacts;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

@Service
public class BagTraceIdentityQueryService
        implements BagTraceIdentityQueryPort {

    private final JdbcTemplate jdbc;

    public BagTraceIdentityQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public BagTraceIdentityFacts resolve(BagTraceIdentityBatchRef batchRef) {
        Map<UUID, BagTraceIdentityFacts.User> result = new LinkedHashMap<>();
        batchRef.consumeOnce((token, tenantId, organizationId, userId) -> {
            BagTraceIdentityFacts.User user = jdbc.query("""
                            SELECT organization_user_uid,
                                   COALESCE(NULLIF(TRIM(nickname), ''), '匿名用户')
                                       AS nickname,
                                   phone_e164
                            FROM iam_organization_user
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND id = ?
                            """,
                    (rs, ignored) -> new BagTraceIdentityFacts.User(
                            UUID.fromString(rs.getString(
                                    "organization_user_uid")),
                            rs.getString("nickname"),
                            mask(rs.getString("phone_e164"))),
                    tenantId, organizationId, userId).stream().findFirst()
                    .orElseThrow(() -> new IllegalStateException(
                            "bag trace organization user is missing"));
            result.put(token, user);
        });
        return new BagTraceIdentityFacts(result);
    }

    private static String mask(String value) {
        if (value == null || value.length() < 7) {
            return null;
        }
        return value.substring(0, 3)
                + "****"
                + value.substring(value.length() - 4);
    }
}
