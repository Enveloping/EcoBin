package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AutomaticDeviceAcceptanceChallengeServiceTest {

    @Test
    void challengeJoinsTheOnlineInboxTransaction() throws Exception {
        Transactional transaction =
                AutomaticDeviceAcceptanceChallengeService.class
                        .getMethod("requestIfNeeded", long.class)
                        .getAnnotation(Transactional.class);

        assertNotNull(transaction);
        assertEquals(Propagation.REQUIRED, transaction.propagation());
        assertEquals(Isolation.READ_COMMITTED, transaction.isolation());
    }

    @Test
    @SuppressWarnings("unchecked")
    void expiredBlockedChallengeIsCancelledBeforeActiveChallengeCheck()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        var resultSet = mock(java.sql.ResultSet.class);
        when(resultSet.getLong("id")).thenReturn(7L);
        when(resultSet.getString("hardware_sn"))
                .thenReturn("ECM0-SJ4YHY7T5CDYWST0BWGW98ERWM");
        when(resultSet.getString("device_public_code"))
                .thenReturn("Dv_kaJc8dzVthJlsYj9QBm0VBCw");
        when(resultSet.getInt("expected_port_count")).thenReturn(1);
        when(resultSet.getLong("factory_bag_revision")).thenReturn(1L);
        when(resultSet.getBytes("factory_bag_set_sha256"))
                .thenReturn(new byte[32]);
        when(resultSet.getString("acceptance_status"))
                .thenReturn("PENDING");
        when(resultSet.getString("lifecycle_status"))
                .thenReturn("NORMAL");
        when(resultSet.getObject("tenant_id")).thenReturn(null);
        when(resultSet.getString("onenet_connection_status"))
                .thenReturn("ONLINE");
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> {
                    RowMapper<Object> mapper = invocation.getArgument(1);
                    return List.of(mapper.mapRow(resultSet, 0));
                });
        when(jdbc.queryForObject(
                contains("FROM dev_factory_installed_bag"),
                eq(Integer.class),
                any(Object[].class))).thenReturn(1);
        LocalDateTime now = LocalDateTime.of(
                2026, 8, 16, 12, 30, 0, 123_000_000);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);
        when(jdbc.queryForObject(
                contains("FROM ops_reliable_task"),
                eq(Integer.class),
                any(Object[].class))).thenReturn(1);
        TrustedDeviceAcceptanceChallengePort challenges =
                mock(TrustedDeviceAcceptanceChallengePort.class);
        AutomaticDeviceAcceptanceChallengeService service =
                new AutomaticDeviceAcceptanceChallengeService(
                        jdbc,
                        new ObjectMapper(),
                        new DeviceConfigurationCanonicalizer(),
                        mock(DeviceEntryUrlFactory.class),
                        mock(PlatformDeviceAssetTaskRefFactory.class),
                        mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                        challenges,
                        "real",
                        Duration.ofMinutes(5),
                        Duration.ofMinutes(5));

        assertFalse(service.requestIfNeeded(7L));

        verify(challenges).cancelExpiredBlocked(7L, now);
    }
}
