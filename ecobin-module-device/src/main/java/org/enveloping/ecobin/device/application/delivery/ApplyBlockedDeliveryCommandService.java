package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.port.BlockedDeviceCommandBusinessPort;
import org.enveloping.ecobin.device.api.result.BlockedDeviceCommand;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/** Aligns delivery sessions with terminal OneNet dispatch outcomes. */
@Service
public class ApplyBlockedDeliveryCommandService
        implements BlockedDeviceCommandBusinessPort {

    private final JdbcTemplate jdbc;
    private final ApplyDeliveryCommandObservationService observations;

    public ApplyBlockedDeliveryCommandService(
            JdbcTemplate jdbc,
            ApplyDeliveryCommandObservationService observations) {
        this.jdbc = jdbc;
        this.observations = observations;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void applyBlockedDeviceCommand(BlockedDeviceCommand blocked) {
        if (!"START_DELIVERY_SESSION".equals(blocked.commandType())) {
            return;
        }
        List<Target> rows = jdbc.query("""
                        SELECT command_row.tenant_id,
                               command_row.organization_id,
                               command_row.asset_id,
                               command_row.delivery_session_id
                        FROM dev_device_command command_row
                        WHERE command_row.command_uid = ?
                          AND command_row.command_type =
                              'START_DELIVERY_SESSION'
                          AND command_row.delivery_session_id IS NOT NULL
                        """,
                (rs, ignored) -> new Target(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getLong("delivery_session_id")),
                blocked.commandUid().toString());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "blocked delivery command target is missing");
        }
        Target target = rows.getFirst();
        observations.apply(
                target.tenantId(),
                target.organizationId(),
                target.assetId(),
                target.sessionId(),
                blocked.commandType(),
                blocked.certainty()
                        == BlockedDeviceCommand.Certainty
                        .DEFINITELY_NOT_ACCEPTED
                        ? "PRE_START_FAILED" : "FAILED",
                blocked.reasonCode(),
                null,
                blocked.blockedAt());
    }

    private record Target(
            long tenantId,
            long organizationId,
            long assetId,
            long sessionId) {
    }
}
