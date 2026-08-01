package org.enveloping.ecobin.recycling.application.walletquery;

import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.Objects;

@Repository
class PendingDeliveryRewardRepository {

    private final JdbcTemplate jdbc;

    PendingDeliveryRewardRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    long pendingRewardCent(
            DeliveryQueryOrganizationUserRef ownerRef) {
        Objects.requireNonNull(ownerRef, "ownerRef");
        return ownerRef.withDeliveryQueryUserOnce(
                (tenantId, organizationId, userId, ignoredUid) ->
                        jdbc.queryForObject("""
                                        SELECT COALESCE(
                                            SUM(raw_amount_cent),
                                            0
                                        )
                                        FROM rec_delivery_order
                                        WHERE tenant_id = ?
                                          AND organization_id = ?
                                          AND organization_user_id = ?
                                          AND review_status = 'PENDING'
                                          AND raw_calculation_status =
                                              'RELIABLE'
                                          AND raw_amount_cent > 0
                                        """,
                                Long.class,
                                tenantId,
                                organizationId,
                                userId));
    }
}
