package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.CloseRemoteSupportRequest;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.OpenRemoteSupportRequest;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationBinding;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.PlatformMaintenanceSshKeyQueryPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class RemoteSupportSessionTransactionTest {

    private RemoteSupportLeaseStore leases;
    private RemoteSupportSessionService sessions;
    private JdbcTemplate jdbc;
    private DeviceScopeAuthorizationPort authorization;
    private PlatformMaintenanceSshKeyQueryPort sshKeys;
    private GlobalOperationIdempotencyPort idempotency;

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager.initSynchronization();
        leases = mock(RemoteSupportLeaseStore.class);
        jdbc = mock(JdbcTemplate.class);
        authorization = mock(DeviceScopeAuthorizationPort.class);
        sshKeys = mock(PlatformMaintenanceSshKeyQueryPort.class);
        idempotency = mock(GlobalOperationIdempotencyPort.class);
        RemoteSupportBootstrapProperties properties =
                new RemoteSupportBootstrapProperties();
        properties.setEnabled(true);
        sessions = new RemoteSupportSessionService(
                jdbc,
                new ObjectMapper(),
                authorization,
                sshKeys,
                idempotency,
                mock(AuditPort.class),
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(ReliableDeviceTaskProofPort.class),
                mock(DeviceConfigurationCanonicalizer.class),
                leases,
                mock(OpenSshMaintenanceCertificateSigner.class),
                properties);
    }

    @AfterEach
    void tearDown() {
        TransactionSynchronizationManager.clearSynchronization();
    }

    @Test
    void desiredLeaseIsNotPublishedUntilDatabaseCommit() {
        RemoteSupportLeaseStore.Lease lease = lease(UUID.randomUUID());

        sessions.registerAfterCommitLeasePublish(lease);
        verifyNoInteractions(leases);

        afterCommit();

        verify(leases).synchronizeDesired(lease);
    }

    @Test
    void delayedRevokeIsSessionAwareAfterDatabaseCommit() {
        UUID sessionUid = UUID.randomUUID();

        sessions.registerAfterCommitLeaseRevoke(sessionUid, 22011);
        verifyNoInteractions(leases);

        afterCommit();

        verify(leases).revoke(sessionUid, 22011);
        verify(leases, never()).removeDesired(22011);
    }

    @Test
    @SuppressWarnings({"unchecked", "rawtypes"})
    void globalConflictStopsBeforeMaintenanceKeyAndAssetSideEffects() {
        UUID operationUid = UUID.randomUUID();
        UUID actorUid = UUID.randomUUID();
        UUID maintenanceKeyUid = UUID.randomUUID();
        when(authorization.authorize(any())).thenReturn(
                new AuthorizedDeviceScope(
                        true,
                        actorUid,
                        UUID.randomUUID(),
                        "Platform administrator",
                        null,
                        null,
                        true,
                        true,
                        null));
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                eq(operationUid.toString())))
                .thenReturn(List.of());
        when(idempotency.claim(any())).thenThrow(new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "operation binding differs"));

        TargetApiException conflict = assertThrows(
                TargetApiException.class,
                () -> sessions.open(
                        operationUid,
                        "SN-REMOTE-0001",
                        new OpenRemoteSupportRequest(
                                maintenanceKeyUid,
                                900,
                                "diagnose connectivity")));

        assertEquals(409, conflict.status());
        assertEquals("COMMON.IDEMPOTENCY_KEY_CONFLICT", conflict.code());
        var binding = org.mockito.ArgumentCaptor.forClass(
                GlobalOperationBinding.class);
        verify(idempotency).claim(binding.capture());
        assertEquals(operationUid, binding.getValue().operationUid());
        assertEquals("PLATFORM_ADMIN", binding.getValue().actorKind());
        assertEquals(actorUid, binding.getValue().actorUid());
        assertEquals("device.remote-support.open",
                binding.getValue().actionCode());
        assertEquals("remote-support-session",
                binding.getValue().targetType());
        assertEquals("SN-REMOTE-0001",
                binding.getValue().targetStableKey());
        verifyNoInteractions(sshKeys, leases);
    }

    @Test
    @SuppressWarnings({"unchecked", "rawtypes"})
    void closeGlobalConflictStopsBeforeSessionLockAndLeaseRevoke() {
        UUID operationUid = UUID.randomUUID();
        UUID sessionUid = UUID.randomUUID();
        UUID actorUid = UUID.randomUUID();
        when(authorization.authorize(any())).thenReturn(
                new AuthorizedDeviceScope(
                        true,
                        actorUid,
                        UUID.randomUUID(),
                        "Platform administrator",
                        null,
                        null,
                        true,
                        true,
                        null));
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                eq(operationUid.toString())))
                .thenReturn(List.of());
        when(idempotency.claim(any())).thenThrow(new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "operation binding differs"));

        TargetApiException conflict = assertThrows(
                TargetApiException.class,
                () -> sessions.close(
                        operationUid,
                        sessionUid,
                        new CloseRemoteSupportRequest("maintenance done")));

        assertEquals(409, conflict.status());
        assertEquals("COMMON.IDEMPOTENCY_KEY_CONFLICT", conflict.code());
        var binding = org.mockito.ArgumentCaptor.forClass(
                GlobalOperationBinding.class);
        verify(idempotency).claim(binding.capture());
        assertEquals(operationUid, binding.getValue().operationUid());
        assertEquals("device.remote-support.close",
                binding.getValue().actionCode());
        assertEquals(sessionUid.toString(),
                binding.getValue().targetStableKey());
        verifyNoInteractions(leases);
    }

    private static void afterCommit() {
        for (TransactionSynchronization synchronization
                : TransactionSynchronizationManager
                        .getSynchronizations()) {
            synchronization.afterCommit();
        }
    }

    private static RemoteSupportLeaseStore.Lease lease(UUID sessionUid) {
        Instant createdAt = Instant.parse("2026-08-15T12:00:00Z");
        return new RemoteSupportLeaseStore.Lease(
                sessionUid,
                "SN-REMOTE-0001",
                22011,
                "ssh-ed25519",
                "A".repeat(68),
                "SHA256:" + "B".repeat(43),
                createdAt,
                createdAt.plusSeconds(900));
    }
}
