package org.enveloping.ecobin.device.application.factory;

import org.enveloping.ecobin.device.api.port.BagCodeAdmissionPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.FactoryAcceptanceView;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.InstallFactoryBagRequest;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationBinding;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationClaim;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationDigests;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationResult;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.FactoryOperatorPersistenceRef;
import org.enveloping.ecobin.identity.api.port.FactoryMiniappAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedFactoryOperatorIdentity;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class FactoryAcceptanceIdempotencyTest {

    private static final String DEVICE_CODE =
            "Dv_ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    private static final String BAG_CODE = "EB1.TEST.FACTORY.BAG";

    private JdbcTemplate jdbc;
    private GlobalOperationIdempotencyPort idempotency;
    private AuditPort auditPort;
    private FactoryOperatorPersistenceRef operatorRef;
    private FactoryAcceptanceService service;
    private UUID actorUid;

    @BeforeEach
    void setUp() {
        jdbc = mock(JdbcTemplate.class);
        idempotency = mock(GlobalOperationIdempotencyPort.class);
        BagCodeAdmissionPort bagAdmission = mock(BagCodeAdmissionPort.class);
        when(bagAdmission.authenticate(BAG_CODE)).thenReturn(Optional.of(
                new BagCodeAdmissionPort.AuthenticatedBagCode(
                        BAG_CODE, "K1")));
        FactoryMiniappAuthorizationPort authorization =
                mock(FactoryMiniappAuthorizationPort.class);
        actorUid = UUID.randomUUID();
        operatorRef = mock(FactoryOperatorPersistenceRef.class);
        AuthorizedFactoryOperatorIdentity actor =
                new AuthorizedFactoryOperatorIdentity(
                        actorUid,
                        "FACTORY_TEST",
                        UUID.randomUUID(),
                        "Factory test operator",
                        Set.of(FactoryMiniappAuthorizationPort.BAG_INSTALL),
                        operatorRef);
        when(authorization.requireCapability(
                FactoryMiniappAuthorizationPort.BAG_INSTALL))
                .thenReturn(actor);
        auditPort = mock(AuditPort.class);
        service = new FactoryAcceptanceService(
                jdbc,
                new ObjectMapper(),
                bagAdmission,
                authorization,
                auditPort,
                mock(TrustedDeviceAcceptanceChallengePort.class),
                idempotency);
    }

    @Test
    @SuppressWarnings("unchecked")
    void claimsGlobalOperationBeforeLockingFactoryAsset() {
        UUID operationUid = UUID.randomUUID();
        when(idempotency.claim(any())).thenReturn(
                GlobalOperationClaim.acquired());
        doReturn(List.of()).when(jdbc).query(
                contains("FOR UPDATE"),
                any(RowMapper.class),
                any(Object[].class));

        assertThatThrownBy(() -> service.install(
                operationUid,
                DEVICE_CODE,
                new InstallFactoryBagRequest(1, BAG_CODE)))
                .isInstanceOf(TargetApiException.class)
                .extracting("code")
                .isEqualTo("RESOURCE.NOT_FOUND");

        ArgumentCaptor<GlobalOperationBinding> binding =
                ArgumentCaptor.forClass(GlobalOperationBinding.class);
        InOrder order = inOrder(idempotency, jdbc);
        order.verify(idempotency).claim(binding.capture());
        order.verify(jdbc).query(
                contains("FOR UPDATE"),
                any(RowMapper.class),
                any(Object[].class));
        assertThat(binding.getValue()).satisfies(value -> {
            assertThat(value.operationUid()).isEqualTo(operationUid);
            assertThat(value.actorKind()).isEqualTo("FACTORY_OPERATOR");
            assertThat(value.actorUid()).isEqualTo(actorUid);
            assertThat(value.scopeDigest()).isEqualTo(
                    GlobalOperationDigests.platformScope());
            assertThat(value.actionCode()).isEqualTo(
                    "device.factory-bag.install");
            assertThat(value.targetType()).isEqualTo(
                    "device-factory-bag");
            assertThat(value.targetStableKey()).isEqualTo(
                    DEVICE_CODE + ":1");
            assertThat(value.requestDigest()).matches("[0-9a-f]{64}");
        });
    }

    @Test
    @SuppressWarnings("unchecked")
    void locksImmutableIssuedLabelWithSelectOnlyShareLock()
            throws Exception {
        UUID operationUid = UUID.randomUUID();
        when(idempotency.claim(any())).thenReturn(
                GlobalOperationClaim.acquired());
        ResultSet asset = assetRow(UUID.randomUUID());
        doAnswer(invocation -> {
            RowMapper<Object> mapper = invocation.getArgument(1);
            return List.of(mapper.mapRow(asset, 0));
        }).when(jdbc).query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class));
        AtomicReference<String> labelSql = new AtomicReference<>();
        doAnswer(invocation -> {
            labelSql.set(invocation.getArgument(0));
            return List.of();
        }).when(jdbc).query(
                contains("FROM rec_bag_label_item"),
                any(RowMapper.class),
                any(Object[].class));

        assertThatThrownBy(() -> service.install(
                operationUid,
                DEVICE_CODE,
                new InstallFactoryBagRequest(1, BAG_CODE)))
                .isInstanceOf(TargetApiException.class)
                .extracting("code")
                .isEqualTo("RECYCLING.BAG_LABEL_NOT_ISSUED");

        assertThat(labelSql.get())
                .contains("FOR SHARE")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    @SuppressWarnings("unchecked")
    void completedGlobalClaimReplaysWithoutLockingOrWritingTarget()
            throws Exception {
        UUID operationUid = UUID.randomUUID();
        UUID assetUid = UUID.randomUUID();
        AtomicReference<GlobalOperationBinding> claimed =
                new AtomicReference<>();
        when(idempotency.claim(any())).thenAnswer(invocation -> {
            claimed.set(invocation.getArgument(0));
            return GlobalOperationClaim.replay(new GlobalOperationResult(
                    assetUid, "INSTALLED", 4));
        });
        doAnswer(invocation -> {
            if (claimed.get() == null) {
                return List.of();
            }
            return List.of(java.util.HexFormat.of().parseHex(
                    claimed.get().requestDigest()));
        }).when(jdbc).query(
                contains("FROM dev_factory_installed_bag_change"),
                any(RowMapper.class),
                any(Object[].class));
        SuccessfulAudit audit = mock(SuccessfulAudit.class);
        when(audit.actorKind()).thenReturn(AuditActorKind.FACTORY_OPERATOR);
        when(audit.scopeKind()).thenReturn(AuditScopeKind.PLATFORM);
        when(audit.actionCode()).thenReturn("device.factory-bag.install");
        when(audit.targetType()).thenReturn("device-factory-bag");
        when(audit.targetStableKey()).thenReturn(DEVICE_CODE + ":1");
        when(audit.factoryOperatorId()).thenReturn(73L);
        when(auditPort.findSuccessful(operationUid))
                .thenReturn(Optional.of(audit));
        doAnswer(invocation -> {
            FactoryOperatorPersistenceRef.ForeignKeyWriter writer =
                    invocation.getArgument(0);
            writer.write(73L);
            return null;
        }).when(operatorRef).writeForeignKeyTo(any());
        ResultSet asset = assetRow(assetUid);
        doAnswer(invocation -> {
            RowMapper<Object> mapper = invocation.getArgument(1);
            return List.of(mapper.mapRow(asset, 0));
        }).when(jdbc).query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class));
        doReturn(List.of()).when(jdbc).query(
                contains("FROM dev_factory_installed_bag bag"),
                any(RowMapper.class),
                any(Object[].class));

        FactoryAcceptanceView replay = service.install(
                operationUid,
                DEVICE_CODE,
                new InstallFactoryBagRequest(1, BAG_CODE));

        verify(idempotency).claim(any());
        assertThat(claimed.get().operationUid()).isEqualTo(operationUid);
        assertThat(replay.deviceCode()).isEqualTo(DEVICE_CODE);
        assertThat(replay.acceptanceStatus()).isEqualTo("PENDING");
        assertThat(replay.factoryBags()).isEmpty();
        verify(jdbc, never()).update(anyString(), any(Object[].class));
        verify(idempotency, never()).succeed(any(), any());
    }

    private static ResultSet assetRow(UUID assetUid) throws Exception {
        ResultSet row = mock(ResultSet.class);
        when(row.getLong("id")).thenReturn(41L);
        when(row.getString("asset_uid")).thenReturn(assetUid.toString());
        when(row.getString("device_public_code")).thenReturn(DEVICE_CODE);
        when(row.getString("hardware_sn")).thenReturn("HW-FACTORY-TEST");
        when(row.getInt("expected_port_count")).thenReturn(1);
        when(row.getString("acceptance_status")).thenReturn("PENDING");
        when(row.getString("lifecycle_status")).thenReturn("NORMAL");
        when(row.getObject("tenant_id")).thenReturn(null);
        when(row.getLong("factory_bag_revision")).thenReturn(4L);
        return row;
    }
}
