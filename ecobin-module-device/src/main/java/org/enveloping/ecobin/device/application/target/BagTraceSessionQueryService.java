package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.persistence.BagTraceCycleRef;
import org.enveloping.ecobin.device.api.persistence.BagTraceSessionSelectionRef;
import org.enveloping.ecobin.device.api.persistence.DeviceOwnedBagTraceSessionSelectionRefFactory;
import org.enveloping.ecobin.device.api.port.BagTraceSessionQueryPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.ZoneOffset;

@Service
public class BagTraceSessionQueryService
        implements BagTraceSessionQueryPort {

    private final JdbcTemplate jdbc;
    private final DeviceOwnedBagTraceSessionSelectionRefFactory refs;

    public BagTraceSessionQueryService(
            JdbcTemplate jdbc,
            DeviceOwnedBagTraceSessionSelectionRefFactory refs) {
        this.jdbc = jdbc;
        this.refs = refs;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public BagTraceSessionSelectionRef sessions(BagTraceCycleRef cycle) {
        return cycle.consumeOnce((tenant, organization, port, start, end) ->
                refs.issue(jdbc.query("""
                                SELECT id, created_at
                                FROM dev_delivery_session
                                WHERE tenant_id = ?
                                  AND organization_id = ?
                                  AND port_id = ?
                                  AND created_at >= ?
                                  AND (? IS NULL OR created_at < ?)
                                ORDER BY created_at, id
                                """,
                        (rs, ignored) -> new BagTraceSessionSelectionRef.Session(
                                rs.getLong("id"),
                                rs.getObject("created_at", LocalDateTime.class)
                                        .toInstant(ZoneOffset.UTC)),
                        tenant, organization, port,
                        LocalDateTime.ofInstant(start, ZoneOffset.UTC),
                        end == null ? null
                                : LocalDateTime.ofInstant(end, ZoneOffset.UTC),
                        end == null ? null
                                : LocalDateTime.ofInstant(end, ZoneOffset.UTC))));
    }
}
