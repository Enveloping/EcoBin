package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.PlatformMaintenanceSshKeyQueryPort;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.util.UUID;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

class RemoteSupportSessionTransactionTest {

    private RemoteSupportLeaseStore leases;
    private RemoteSupportSessionService sessions;

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager.initSynchronization();
        leases = mock(RemoteSupportLeaseStore.class);
        sessions = new RemoteSupportSessionService(
                mock(JdbcTemplate.class),
                new ObjectMapper(),
                mock(DeviceScopeAuthorizationPort.class),
                mock(PlatformMaintenanceSshKeyQueryPort.class),
                mock(AuditPort.class),
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(ReliableDeviceTaskProofPort.class),
                mock(DeviceConfigurationCanonicalizer.class),
                leases,
                mock(OpenSshMaintenanceCertificateSigner.class),
                new RemoteSupportBootstrapProperties());
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
