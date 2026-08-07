package org.enveloping.ecobin.recycling.application.photo;

import org.enveloping.ecobin.device.api.port.PhotoGrantWorkVerificationPort;
import org.enveloping.ecobin.device.api.result.PhotoGrantWorkVerification;
import org.enveloping.ecobin.device.api.result.TrustedPhotoGrantWork;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PhotoGrantWorkVerificationService
        implements PhotoGrantWorkVerificationPort {

    private final JdbcTemplate jdbc;

    public PhotoGrantWorkVerificationService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public PhotoGrantWorkVerification verify(
            TrustedPhotoGrantWork work) {
        Integer workCount =
                "DELIVERY_SESSION".equals(work.workType())
                        ? jdbc.queryForObject("""
                                        SELECT COUNT(*)
                                        FROM dev_delivery_session
                                        WHERE tenant_id = ?
                                          AND organization_id = ?
                                          AND asset_id = ?
                                          AND session_uid = ?
                                        """,
                                Integer.class,
                                work.tenantId(),
                                work.organizationId(),
                                work.assetId(),
                                work.workUid().toString())
                        : jdbc.queryForObject("""
                                        SELECT COUNT(*)
                                        FROM rec_clean_operation
                                        WHERE tenant_id = ?
                                          AND organization_id = ?
                                          AND asset_id = ?
                                          AND operation_uid = ?
                                        """,
                                Integer.class,
                                work.tenantId(),
                                work.organizationId(),
                                work.assetId(),
                                work.workUid().toString());
        if (workCount == null || workCount != 1) {
            return PhotoGrantWorkVerification.UNKNOWN_WORK;
        }
        int terminalSlots = 0;
        for (String slot : work.requestedSlots()) {
            Integer count = jdbc.queryForObject("""
                            SELECT COUNT(*)
                            FROM rec_photo_terminal_fact
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND asset_id = ?
                              AND work_type = ?
                              AND work_uid = ?
                              AND position = ?
                            """,
                    Integer.class,
                    work.tenantId(),
                    work.organizationId(),
                    work.assetId(),
                    work.workType(),
                    work.workUid().toString(),
                    slot);
            if (count != null && count == 1) {
                terminalSlots++;
            }
        }
        return terminalSlots == work.requestedSlots().size()
                ? PhotoGrantWorkVerification
                .ALL_REQUESTED_SLOTS_TERMINAL
                : PhotoGrantWorkVerification.NEEDS_GRANT;
    }
}
