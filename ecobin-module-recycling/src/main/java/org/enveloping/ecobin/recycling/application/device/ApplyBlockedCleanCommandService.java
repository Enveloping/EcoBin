package org.enveloping.ecobin.recycling.application.device;

import org.enveloping.ecobin.device.api.port.BlockedDeviceCommandBusinessPort;
import org.enveloping.ecobin.device.api.result.BlockedDeviceCommand;
import org.enveloping.ecobin.device.api.result.TrustedCleanCommandObservation;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/** Aligns cleaning operations with terminal OneNet dispatch outcomes. */
@Service
public class ApplyBlockedCleanCommandService
        implements BlockedDeviceCommandBusinessPort {

    private final JdbcTemplate jdbc;
    private final ApplyCleanCommandObservationService observations;

    public ApplyBlockedCleanCommandService(
            JdbcTemplate jdbc,
            ApplyCleanCommandObservationService observations) {
        this.jdbc = jdbc;
        this.observations = observations;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void applyBlockedDeviceCommand(BlockedDeviceCommand blocked) {
        if (!"START_CLEAN_OPERATION".equals(blocked.commandType())) {
            return;
        }
        List<Target> rows = jdbc.query("""
                        SELECT command_row.tenant_id,
                               command_row.organization_id,
                               command_row.asset_id,
                               command_row.clean_operation_id
                        FROM dev_device_command command_row
                        WHERE command_row.command_uid = ?
                          AND command_row.command_type =
                              'START_CLEAN_OPERATION'
                          AND command_row.clean_operation_id IS NOT NULL
                        """,
                (rs, ignored) -> new Target(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getLong("clean_operation_id")),
                blocked.commandUid().toString());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "blocked clean command target is missing");
        }
        Target target = rows.getFirst();
        observations.applyCleanCommandObservation(
                new TrustedCleanCommandObservation(
                        target.tenantId(),
                        target.organizationId(),
                        target.assetId(),
                        target.operationId(),
                        blocked.commandType(),
                        blocked.certainty()
                                == BlockedDeviceCommand.Certainty
                                .DEFINITELY_NOT_ACCEPTED
                                ? "PRE_START_FAILED" : "FAILED",
                        blocked.reasonCode(),
                        null,
                        blocked.blockedAt()));
    }

    private record Target(
            long tenantId,
            long organizationId,
            long assetId,
            long operationId) {
    }
}
