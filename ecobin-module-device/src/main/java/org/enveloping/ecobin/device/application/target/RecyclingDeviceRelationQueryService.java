package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.persistence.RecyclingDeviceRelationBatchRef;
import org.enveloping.ecobin.device.api.port.RecyclingDeviceRelationQueryPort;
import org.enveloping.ecobin.device.api.result.RecyclingDeviceRelationFacts;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.UUID;

@Service
public class RecyclingDeviceRelationQueryService
        implements RecyclingDeviceRelationQueryPort {

    private final JdbcTemplate jdbc;

    public RecyclingDeviceRelationQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public RecyclingDeviceRelationFacts resolve(
            RecyclingDeviceRelationBatchRef batch) {
        List<PortKey> ports = new ArrayList<>();
        List<SessionKey> sessions = new ArrayList<>();
        List<FactKey> facts = new ArrayList<>();
        batch.consumeOnce(new RecyclingDeviceRelationBatchRef.EntrySink() {
            @Override
            public void port(UUID token, long tenant, long organization,
                             long port) {
                ports.add(new PortKey(token, tenant, organization, port));
            }

            @Override
            public void deliverySession(
                    UUID token, long tenant, long organization, long session) {
                sessions.add(new SessionKey(
                        token, tenant, organization, session));
            }

            @Override
            public void fullnessStateFact(
                    UUID token, long tenant, long organization, long fact) {
                facts.add(new FactKey(token, tenant, organization, fact));
            }
        });
        return new RecyclingDeviceRelationFacts(
                resolvePorts(ports), resolveSessions(sessions),
                resolveFacts(facts));
    }

    private LinkedHashMap<UUID, RecyclingDeviceRelationFacts.Port>
    resolvePorts(List<PortKey> keys) {
        LinkedHashMap<UUID, RecyclingDeviceRelationFacts.Port> result =
                new LinkedHashMap<>();
        for (PortKey key : keys) {
            var value = jdbc.query("""
                            SELECT deployment.public_code, port.port_no
                            FROM dev_port port
                            JOIN dev_device_deployment deployment
                              ON deployment.id = port.deployment_id
                            WHERE port.tenant_id = ?
                              AND port.organization_id = ?
                              AND port.id = ?
                            """,
                    (rs, ignored) -> new RecyclingDeviceRelationFacts.Port(
                            rs.getString("public_code"),
                            rs.getInt("port_no")),
                    key.tenant(), key.organization(), key.key()).stream()
                    .findFirst().orElseThrow(() -> new IllegalStateException(
                            "recycling port relationship is invalid"));
            result.put(key.token(), value);
        }
        return result;
    }

    private LinkedHashMap<UUID, RecyclingDeviceRelationFacts.DeliverySession>
    resolveSessions(List<SessionKey> keys) {
        LinkedHashMap<UUID, RecyclingDeviceRelationFacts.DeliverySession>
                result = new LinkedHashMap<>();
        for (SessionKey key : keys) {
            var value = jdbc.query("""
                            SELECT created_at
                            FROM dev_delivery_session
                            WHERE tenant_id = ? AND organization_id = ?
                              AND id = ?
                            """,
                    (rs, ignored) -> new RecyclingDeviceRelationFacts
                            .DeliverySession(rs.getObject(
                            "created_at", LocalDateTime.class)
                            .toInstant(ZoneOffset.UTC)),
                    key.tenant(), key.organization(), key.key()).stream()
                    .findFirst().orElseThrow(() -> new IllegalStateException(
                            "delivery session relationship is invalid"));
            result.put(key.token(), value);
        }
        return result;
    }

    private LinkedHashMap<UUID, RecyclingDeviceRelationFacts.FullnessStateFact>
    resolveFacts(List<FactKey> keys) {
        LinkedHashMap<UUID, RecyclingDeviceRelationFacts.FullnessStateFact>
                result = new LinkedHashMap<>();
        for (FactKey key : keys) {
            var value = jdbc.query("""
                            SELECT fullness_mode, fullness_sensor_kind,
                                   fullness_sensor_value, total_weight_g,
                                   baseline_weight_g,
                                   configured_full_weight_g,
                                   fullness_percent_hundredths, sample_count,
                                   measurement_elapsed_ms, confirmation_basis
                            FROM dev_fullness_state_fact
                            WHERE tenant_id = ? AND organization_id = ?
                              AND id = ?
                            """,
                    (rs, ignored) -> new RecyclingDeviceRelationFacts
                            .FullnessStateFact(
                            rs.getString("fullness_mode"),
                            rs.getString("fullness_sensor_kind"),
                            rs.getString("fullness_sensor_value"),
                            rs.getLong("total_weight_g"),
                            (Long) rs.getObject("baseline_weight_g"),
                            rs.getLong("configured_full_weight_g"),
                            (Long) rs.getObject(
                                    "fullness_percent_hundredths"),
                            rs.getLong("sample_count"),
                            rs.getLong("measurement_elapsed_ms"),
                            rs.getString("confirmation_basis")),
                    key.tenant(), key.organization(), key.key()).stream()
                    .findFirst().orElseThrow(() -> new IllegalStateException(
                            "fullness state fact relationship is invalid"));
            result.put(key.token(), value);
        }
        return result;
    }

    private record PortKey(UUID token, long tenant, long organization,
                           long key) { }
    private record SessionKey(UUID token, long tenant, long organization,
                              long key) { }
    private record FactKey(UUID token, long tenant, long organization,
                           long key) { }
}
