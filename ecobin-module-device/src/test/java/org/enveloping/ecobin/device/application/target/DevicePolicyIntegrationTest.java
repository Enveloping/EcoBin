package org.enveloping.ecobin.device.application.target;

import tools.jackson.databind.ObjectMapper;
import org.enveloping.ecobin.device.web.v1.DeviceModels.*;
import org.enveloping.ecobin.framework.audit.*;
import org.enveloping.ecobin.framework.reliability.*;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeviceScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DelegatingDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.Timestamp;
import java.sql.Connection;
import java.sql.SQLException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** Exercises actual SQL and complete configuration publication; no IoT commands leave this fixture. */
public class DevicePolicyIntegrationTest {
    private JdbcTemplate jdbc;
    private TransactionTemplate tx;
    private TargetDeviceApplication app;
    private AutomaticDeviceActivationService activation;
    private InitialDeviceConfigurationFactory initial;
    private InitialDeviceConfigurationProperties initialProperties;
    private DeviceScopeAuthorizationPort authorization;
    private AuditPort audit;
    private long actorTenant = 7L;
    private final UUID principal = UUID.randomUUID();
    private final UUID session = UUID.randomUUID();

    public static Timestamp utcTimestamp(int ignored) { return Timestamp.from(Instant.now()); }
    public static String hex(byte[] value) { return value == null ? null : HexFormat.of().formatHex(value); }

    @BeforeEach
    void setup() {
        var h2 = new DriverManagerDataSource("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        // H2 CAST(text AS JSON) stores a JSON string, unlike MySQL's parsed object.
        // Adapt only this fixture's SQL, never relax production frozen-envelope validation.
        var source = new DelegatingDataSource(h2) {
            @Override
            public Connection getConnection() throws SQLException {
                Connection connection = super.getConnection();
                return (Connection) Proxy.newProxyInstance(Connection.class.getClassLoader(),
                        new Class<?>[]{Connection.class}, (proxy, method, arguments) -> {
                            if (method.getName().equals("prepareStatement") && arguments[0] instanceof String sql) {
                                arguments[0] = sql.replace("CAST(? AS JSON)", "? FORMAT JSON");
                            }
                            try {
                                return method.invoke(connection, arguments);
                            } catch (InvocationTargetException error) {
                                throw error.getCause();
                            }
                        });
            }
        };
        new ResourceDatabasePopulator(new ClassPathResource("device-policy-fixture.sql")).execute(source);
        jdbc = new JdbcTemplate(source);
        tx = new TransactionTemplate(new DataSourceTransactionManager(source));
        var mapper = new ObjectMapper();
        var canonicalizer = new DeviceConfigurationCanonicalizer();
        var runtime = new RuntimeSnapshotPolicyProvider(jdbc);
        var tasks = mock(ReliableDeviceTaskRegistrationPort.class);
        var refs = mock(DeviceCommandTaskRefFactory.class);
        when(refs.issue(anyLong(), anyLong(), anyLong(), anyLong())).thenReturn(mock(DeviceCommandTaskRef.class));
        doAnswer(call -> {
            ReliableDeviceTaskRegistration task = call.getArgument(0);
            jdbc.update("INSERT INTO ops_reliable_task VALUES (?, ?, ?, 'PENDING')", task.taskType(), task.targetType(), task.targetStableKey());
            return null;
        }).when(tasks).register(any());
        initialProperties = new InitialDeviceConfigurationProperties();
        initial = new InitialDeviceConfigurationFactory(initialProperties);
        activation = new AutomaticDeviceActivationService(jdbc, mapper, canonicalizer, runtime, initial, tasks, refs);
        authorization = mock(DeviceScopeAuthorizationPort.class);
        when(authorization.authorize(any())).thenAnswer(call -> {
            var query = (org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery) call.getArgument(0);
            var scopeRef = mock(DeviceScopePersistenceRef.class);
            doAnswer(write -> {
                DeviceScopePersistenceRef.ForeignKeyWriter writer = write.getArgument(0);
                writer.write(query.platformPath() ? null : actorTenant, query.platformPath() || query.organizationCode() == null ? null : ("org-2".equals(query.organizationCode()) ? 10L : 9L),
                        query.platformPath() ? 1L : null, query.platformPath() ? null : actorTenant == 7 ? 2L : 3L);
                return null;
            }).when(scopeRef).writeForeignKeysTo(any());
            return new AuthorizedDeviceScope(query.platformPath(), principal, session, "测试操作者", "tenant", query.organizationCode(), true, true, scopeRef);
        });
        audit = mock(AuditPort.class);
        Map<UUID, SuccessfulAudit> auditRows = new ConcurrentHashMap<>();
        when(audit.findSuccessful(any())).thenAnswer(call -> Optional.ofNullable(auditRows.get(call.getArgument(0))));
        doAnswer(call -> {
            AuditEntry entry = call.getArgument(0);
            auditRows.put(entry.operationUid(), new SuccessfulAudit(entry.operationUid(), entry.actorKind(), entry.platformAdminId(),
                    entry.staffAccountId(), entry.organizationUserId(), entry.scopeKind(), entry.tenantId(), entry.organizationId(),
                    entry.actionCode(), entry.targetType(), entry.targetStableKey(), entry.safeChangeSummaryJson()));
            return null;
        }).when(audit).append(any());
        app = new TargetDeviceApplication(jdbc, authorization, audit, mapper, activation, canonicalizer, runtime, tasks,
                mock(ReliableDeviceTaskStatusPort.class), mock(ReliableTaskWakePort.class), refs, "test-product", null, null);
    }

    @Test
    void releaseIsVersionedAuditedAndIdempotent() {
        var uid = UUID.randomUUID();
        var request = platformRequest(1L, "WEIGHT_ONLY", "80.125", "统一更新");
        var released = tx.execute(status -> app.releaseDevicePolicy(true, uid, request));
        assertThat(released.version()).isEqualTo(2);
        assertThat(released.fullnessWeightKg()).isEqualTo("80.125");
        assertThat(released.rolloutStatus()).isEqualTo("DONE");
        assertThat(tx.<DevicePolicyView>execute(status -> app.releaseDevicePolicy(true, uid, request))).isEqualTo(released);
        assertThatThrownBy(() -> tx.execute(status -> app.releaseDevicePolicy(true, UUID.randomUUID(), request)))
                .isInstanceOf(TargetApiException.class).hasMessageContaining("请刷新");
        assertThatThrownBy(() -> tx.execute(status -> app.releaseDevicePolicy(true, uid,
                platformRequest(2L, "INFRARED_ONLY", "80.125", "另一请求"))))
                .isInstanceOf(TargetApiException.class);
        verify(audit, times(1)).append(any());
        verify(authorization, atLeastOnce()).authorize(argThat(query -> query.platformPath() && query.requiredCapability().equals("device.manage")));
    }

    @Test
    void deniedCallerCannotReadOrPublishGlobalPolicy() {
        doThrow(new TargetApiException(403, "FORBIDDEN", "无权限", false, Map.of())).when(authorization).authorize(any());
        assertThatThrownBy(() -> tx.execute(status -> app.devicePolicy(true))).isInstanceOf(TargetApiException.class);
        assertThatThrownBy(() -> tx.execute(status -> app.releaseDevicePolicy(true, UUID.randomUUID(),
                platformRequest(1L, "WEIGHT_ONLY", "80", "无权修改")))).isInstanceOf(TargetApiException.class);
        assertThat(jdbc.queryForObject("SELECT policy_version FROM dev_device_default_policy", Long.class)).isEqualTo(1);
    }

    @Test
    void initialAndLaterPriceChangesUseOneRuleForEveryPort() {
        release("WEIGHT_ONLY", "75.321");
        asset(1, "NORMAL", "PASSED", 9L);
        initialProperties.setUnitPriceYuanPerKg("0.9000");
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        assertConfiguration(1, 1, "WEIGHT_ONLY", 75321, 2);
        initialProperties.setUnitPriceYuanPerKg("0.4500");
        var defaults = initial.create("EC-M0", 2); // carries the old per-port 50 kg defaults
        var ports = defaults.ports().stream().map(port -> new ConfigurationPortRequest(port.portNo(), "新名称" + port.portNo(), port.enabled(), "0.9000", port.fullnessMode(), port.fullnessWeightKg(), port.deliverySettleDelayMs(), port.fullnessInitialDelayMs(), port.fullnessRecheckDelayMs(), port.doorAutoCloseTimeoutMs(), port.fullnessSensorKind(), port.fullnessDistanceThresholdMm(), port.fullnessSampleCount(), port.fullnessMinimumValidSampleCount(), port.fullnessEchoTimeoutUs(), port.weightStableWindowMs(), port.weightMaximumFluctuationGram(), port.weightRequiredSampleCount(), port.weightMeasurementTimeoutMs(), port.weightMinimumGram(), port.weightMaximumGram(), port.calibrationVersion(), port.infraredSampleTimeoutMs(), port.deliveryDoorOperationTimeoutMs())).toList();
        var edits = new ConfigurationReleaseRequest(1L, "调整显示名", defaults.device(), ports);
        tx.execute(status -> app.releaseConfiguration(UUID.randomUUID(), "org", code(1), edits));
        assertConfiguration(1, 2, "WEIGHT_ONLY", 75321, 2);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_port_config_snapshot WHERE config_version_id = 2 AND unit_price_yuan_per_kg = 0.4500", Long.class)).isEqualTo(2);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_port_config_snapshot WHERE config_version_id = 1 AND unit_price_yuan_per_kg = 0.4500", Long.class)).isEqualTo(2);
    }

    @Test
    void rolloutPreservesHistorySkipsUnavailableAndCatchesUpRestoredDevice() {
        asset(1, "NORMAL", "PASSED", 9L);
        asset(2, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> {
            activation.reconcileInCurrentTransaction(1, UUID.randomUUID());
            activation.reconcileInCurrentTransaction(2, UUID.randomUUID());
        });
        jdbc.update("UPDATE dev_device_asset SET lifecycle_status='DISABLED' WHERE id=2");
        asset(3, "RETIRED", "PASSED", 9L);
        asset(4, "NORMAL", "FAILED", 9L);
        asset(5, "NORMAL", "PASSED", null);
        release("INFRARED_ONLY", "65.000");
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isFalse();
        assertConfiguration(1, 1, "INFRARED_OR_WEIGHT", 50000, 1);
        assertConfiguration(1, 2, "INFRARED_ONLY", 65000, 2);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Long.class)).isEqualTo(3);
        var progress = tx.execute(status -> app.devicePolicy(true));
        assertThat(progress.rolloutStatus()).isEqualTo("DONE");
        assertThat(progress.appliedDeviceCount()).isZero();
        assertThat(progress.pendingDeviceCount()).isEqualTo(1);
        jdbc.update("UPDATE dev_config_application SET status='APPLIED' WHERE asset_id=1 AND config_version_id=(SELECT MAX(id) FROM dev_config_version WHERE asset_id=1)");
        assertThat(tx.execute(status -> app.devicePolicy(true)).appliedDeviceCount()).isEqualTo(1);
        jdbc.update("UPDATE dev_device_asset SET lifecycle_status='NORMAL' WHERE id=2");
        tx.executeWithoutResult(status -> app.reconcileAutomaticActivation(2));
        assertConfiguration(2, 2, "INFRARED_ONLY", 65000, 2);
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isFalse();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Long.class)).isEqualTo(4);
    }

    @ParameterizedTest
    @ValueSource(strings={"0", "-1", "0.0001", "NaN", "1e3", "4294967.296", "", "1.0000"})
    void invalidWeightsNeverPublish(String weight) {
        assertThatThrownBy(() -> release("WEIGHT_ONLY", weight)).isInstanceOf(TargetApiException.class);
        assertThat(jdbc.queryForObject("SELECT policy_version FROM dev_device_default_policy", Long.class)).isEqualTo(1);
    }

    @Test
    void rolloutResumesFromStoredCursorAcrossBatchesWithoutDuplicateVersions() {
        for (long id = 1; id <= 101; id++) asset(id, "NORMAL", "PASSED", 9L);
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isTrue();
        assertThat(jdbc.queryForObject("SELECT next_asset_id FROM dev_device_default_policy", Long.class)).isEqualTo(100);
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isFalse();
        var result = tx.execute(status -> app.devicePolicy(true));
        assertThat(result.processedDeviceCount()).isEqualTo(101);
        assertThat(result.publishedDeviceCount()).isEqualTo(101);
        assertThat(result.pendingDeviceCount()).isEqualTo(101);
        assertThat(result.appliedDeviceCount()).isZero();
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isFalse();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Long.class)).isEqualTo(101);
    }

    @Test
    void concurrentEditorsCannotOverwriteASettingTheyDidNotReview() throws Exception {
        var start = new CountDownLatch(1);
        try (var workers = Executors.newFixedThreadPool(2)) {
            var first = workers.submit(() -> raceRelease(start, "60.000"));
            var second = workers.submit(() -> raceRelease(start, "70.000"));
            start.countDown();
            assertThat(List.of(first.get(), second.get())).containsExactlyInAnyOrder("published", "conflict");
        }
        assertThat(jdbc.queryForObject("SELECT policy_version FROM dev_device_default_policy", Long.class)).isEqualTo(2);
        verify(audit, times(1)).append(any());
    }

    private String raceRelease(CountDownLatch start, String weight) throws InterruptedException {
        start.await();
        try {
            release("WEIGHT_ONLY", weight);
            return "published";
        } catch (TargetApiException conflict) {
            assertThat(conflict.getMessage()).contains("请刷新");
            return "conflict";
        }
    }

    private static DevicePolicyReleaseRequest platformRequest(Long version, String mode, String weight, String reason) {
        return new DevicePolicyReleaseRequest(version, version, "DEFAULT", "0.4500", mode, weight, 500L, reason);
    }

    private DevicePolicyView tenantRelease(long version, long defaults, String configurationMode, String price) {
        return tx.execute(status -> app.releaseDevicePolicy(false, UUID.randomUUID(),
                new DevicePolicyReleaseRequest(version, defaults, configurationMode, price, "WEIGHT_ONLY", "80.000", 750L, "租户设置")));
    }

    @Test
    void tenantOverridesAllItsOrganizationsAndPlatformOnlyUpdatesInheritingTenants() {
        asset(1, "NORMAL", "PASSED", 9L);
        asset(2, "NORMAL", "PASSED", 10L);
        asset(3, "NORMAL", "PASSED", 11L);
        jdbc.update("UPDATE dev_device_asset SET tenant_id=8 WHERE id=3");
        jdbc.update("UPDATE dev_port SET tenant_id=8 WHERE asset_id=3");
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        var custom = tenantRelease(0, 1, "CUSTOM", "0.6500");
        assertThat(custom.configurationMode()).isEqualTo("CUSTOM");
        assertThat(custom.targetDeviceCount()).isEqualTo(2);
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        assertThat(jdbc.queryForList("SELECT tenant_device_policy_version_no FROM dev_config_version WHERE version_no=2 ORDER BY asset_id", Long.class)).containsExactly(1L, 1L);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_port_config_snapshot WHERE unit_price_yuan_per_kg=0.6500", Long.class)).isEqualTo(4);
        release("INFRARED_ONLY", "90.000");
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        assertConfiguration(3, 2, "INFRARED_ONLY", 90000, 2);
        assertThat(jdbc.queryForObject("SELECT MAX(version_no) FROM dev_config_version WHERE asset_id IN (1,2)", Long.class)).isEqualTo(2);
        assertThat(app.devicePolicy(false).unitPriceYuanPerKg()).isEqualTo("0.6500");
        assertThat(app.devicePolicy(true).targetDeviceCount()).isEqualTo(1);
        actorTenant = 8;
        assertThat(app.devicePolicy(false).configurationMode()).isEqualTo("INHERIT");
        assertThat(app.devicePolicy(false).unitPriceYuanPerKg()).isEqualTo("0.4500");
        assertThat(app.devicePolicy(false).targetDeviceCount()).isEqualTo(1);
        assertThat(app.devicePolicy(false).processedDeviceCount()).isEqualTo(1);
        assertThat(app.devicePolicy(false).publishedDeviceCount()).isEqualTo(1);
    }

    @Test
    void restoringDefaultsUsesReviewedCurrentDefaultsAndFutureDevicesInherit() {
        tenantRelease(0, 1, "CUSTOM", "0.6500");
        release("INFRARED_ONLY", "90.000");
        assertThatThrownBy(() -> tenantRelease(1, 1, "INHERIT", null)).isInstanceOf(TargetApiException.class);
        tenantRelease(1, 2, "INHERIT", null);
        asset(1, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        assertConfiguration(1, 1, "INFRARED_ONLY", 90000, 2);
        assertThat(jdbc.queryForObject("SELECT unit_price_yuan_per_kg FROM dev_tenant_device_policy WHERE tenant_id=7", String.class)).isNull();
        assertThat(app.devicePolicy(false).configurationMode()).isEqualTo("INHERIT");
        assertThat(app.devicePolicy(false).version()).isEqualTo(2);
    }

    @Test
    void readOnlyInheritanceDoesNotCreateTenantRowsAndNewCustomDevicesUseTenantPrice() {
        assertThat(app.devicePolicy(false).version()).isZero();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_tenant_device_policy", Long.class)).isZero();
        tenantRelease(0, 1, "CUSTOM", "1.2345");
        asset(1, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        assertThat(jdbc.queryForObject("SELECT negative_weight_threshold_g FROM dev_config_version", Long.class)).isEqualTo(750L);
        assertThat(jdbc.queryForList("SELECT unit_price_yuan_per_kg FROM dev_port_config_snapshot", String.class)).containsExactly("1.2345", "1.2345");
        assertThat(jdbc.queryForObject("SELECT device_default_policy_version_no FROM dev_config_version", Long.class)).isNull();
        verify(authorization, atLeastOnce()).authorize(argThat(query -> !query.platformPath() && query.organizationCode() == null && query.tenantCode() == null && query.requiredCapability().equals("device.configuration.manage")));
    }

    @ParameterizedTest
    @ValueSource(strings={"0", "-1", "0.00001", "NaN", "1e3", "429496.7296", "", "1.00000"})
    void invalidTenantPricesNeverPersist(String price) {
        assertThatThrownBy(() -> tenantRelease(0, 1, "CUSTOM", price)).isInstanceOf(TargetApiException.class);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_tenant_device_policy", Long.class)).isZero();
    }

    @Test
    void concurrentFirstTenantEditorsCannotCreateTwoVersions() throws Exception {
        var start = new CountDownLatch(1);
        try (var workers = Executors.newFixedThreadPool(2)) {
            var first = workers.submit(() -> { start.await(); try { tenantRelease(0, 1, "CUSTOM", "0.6500"); return true; } catch (TargetApiException conflict) { return false; } });
            var second = workers.submit(() -> { start.await(); try { tenantRelease(0, 1, "CUSTOM", "0.7500"); return true; } catch (TargetApiException conflict) { return false; } });
            start.countDown();
            assertThat(List.of(first.get(), second.get())).containsExactlyInAnyOrder(true, false);
        }
        assertThat(jdbc.queryForObject("SELECT policy_version FROM dev_tenant_device_policy", Long.class)).isEqualTo(1);
    }

    private void release(String mode, String weight) {
        tx.execute(status -> app.releaseDevicePolicy(true, UUID.randomUUID(), platformRequest(1L, mode, weight, "全局设置")));
    }

    @Test
    void progressNeverCountsAppliedOldVersionsAsTheCurrentTenantPolicy() {
        asset(1, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        jdbc.update("UPDATE dev_config_application SET status='APPLIED'");
        tenantRelease(0, 1, "CUSTOM", "0.6500");
        assertThat(app.devicePolicy(false).appliedDeviceCount()).isZero();
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        assertThat(app.devicePolicy(false).pendingDeviceCount()).isEqualTo(1);
        jdbc.update("UPDATE dev_config_application SET status='EDGE_SAVED' WHERE config_version_id=(SELECT MAX(id) FROM dev_config_version)");
        assertThat(app.devicePolicy(false).edgeSavedDeviceCount()).isEqualTo(1);
        assertThat(app.devicePolicy(false).pendingDeviceCount()).isZero();
        jdbc.update("UPDATE dev_config_application SET status='FAILED' WHERE config_version_id=(SELECT MAX(id) FROM dev_config_version)");
        assertThat(app.devicePolicy(false).failedDeviceCount()).isEqualTo(1);
        jdbc.update("UPDATE ops_reliable_task SET state='BLOCKED'");
        assertThat(app.devicePolicy(false).blockedDeviceCount()).isEqualTo(1);
        assertThat(app.devicePolicy(false).failedDeviceCount()).isZero();
    }

    @Test
    void tenantBatchResumesAndDoesNotGenerateDuplicateDeviceVersions() {
        for (long id=1; id<=101; id++) asset(id, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        tx.executeWithoutResult(status -> app.reconcileDevicePolicyNextBatch());
        tenantRelease(0, 1, "CUSTOM", "0.6500");
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isTrue();
        assertThat(jdbc.queryForObject("SELECT next_asset_id FROM dev_tenant_device_policy", Long.class)).isEqualTo(100);
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isFalse();
        assertThat(app.devicePolicy(false).publishedDeviceCount()).isEqualTo(101);
        assertThat(tx.<Boolean>execute(status -> app.reconcileDevicePolicyNextBatch())).isFalse();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Long.class)).isEqualTo(202);
    }

    @Test
    void protocolBoundsAndMissingTenantValuesAreRejectedBeforePersistence() {
        for (Long threshold : Arrays.asList(null, 0L, -1L, 4294967296L)) {
            assertThatThrownBy(() -> tx.execute(status -> app.releaseDevicePolicy(false, UUID.randomUUID(),
                    new DevicePolicyReleaseRequest(0L, 1L, "CUSTOM", "0.6500", "WEIGHT_ONLY", "50.000", threshold, "异常阈值"))))
                    .isInstanceOf(TargetApiException.class);
        }
        assertThatThrownBy(() -> tenantRelease(0, 1, "CUSTOM", null)).isInstanceOf(TargetApiException.class);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_tenant_device_policy", Long.class)).isZero();
        var result = tx.execute(status -> app.releaseDevicePolicy(false, UUID.randomUUID(),
                new DevicePolicyReleaseRequest(0L, 1L, "CUSTOM", "429496.7295", "WEIGHT_ONLY", "4294967.295", 4294967295L, "协议上限")));
        assertThat(result.unitPriceYuanPerKg()).isEqualTo("429496.7295");
        asset(1, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        assertThat(jdbc.queryForObject("SELECT negative_weight_threshold_g FROM dev_config_version", Long.class)).isEqualTo(4294967295L);
    }

    private static String code(long id) { return "Dv_" + "a".repeat(24) + id; }

    @Test
    void softwareProfileChangesDoNotRepublishConfigurationAndManualPublicationStaysNative() {
        asset(1, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        var oldEnvelope = configurationEnvelope(1);
        var oldHash = jdbc.queryForObject("SELECT content_sha256 FROM dev_config_version WHERE version_no=1", byte[].class);
        recognizedNativeSoftware(1, "BASE_COMPATIBLE");

        tx.executeWithoutResult(status -> app.reconcileAutomaticActivation(1));
        assertThat(configurationEnvelope(1)).isEqualTo(oldEnvelope);
        assertThat(jdbc.queryForObject("SELECT content_sha256 FROM dev_config_version WHERE version_no=1", byte[].class))
                .containsExactly(oldHash);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Integer.class)).isEqualTo(1);

        jdbc.update("UPDATE dev_device_compatibility_projection SET compatibility_status='UNKNOWN'");
        jdbc.update("UPDATE dev_device_software_fact SET uart_state='DISCONNECTED'");
        tx.executeWithoutResult(status -> app.reconcileAutomaticActivation(1));
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Integer.class)).isEqualTo(1);
        var defaults = initial.create("EC-M0", 2);
        tx.execute(status -> app.releaseConfiguration(UUID.randomUUID(), "org", code(1),
                new ConfigurationReleaseRequest(1L, "离线时调整配置", defaults.device(),
                        defaults.ports().stream().map(port -> new ConfigurationPortRequest(port.portNo(), "更新" + port.portNo(),
                                port.enabled(), port.unitPriceYuanPerKg(), port.fullnessMode(), port.fullnessWeightKg(),
                                port.deliverySettleDelayMs(), port.fullnessInitialDelayMs(), port.fullnessRecheckDelayMs(),
                                port.doorAutoCloseTimeoutMs(), port.fullnessSensorKind(), port.fullnessDistanceThresholdMm(),
                                port.fullnessSampleCount(), port.fullnessMinimumValidSampleCount(), port.fullnessEchoTimeoutUs(),
                                port.weightStableWindowMs(), port.weightMaximumFluctuationGram(), port.weightRequiredSampleCount(),
                                port.weightMeasurementTimeoutMs(), port.weightMinimumGram(), port.weightMaximumGram(),
                                port.calibrationVersion(), port.infraredSampleTimeoutMs(), port.deliveryDoorOperationTimeoutMs())).toList())));
        assertThat(new ObjectMapper().readTree(configurationEnvelope(2)).path("payload").path("mcuConfigurationProfile").asString())
                .isEqualTo("UART_V2_SIMPLIFIED");
    }

    @Test
    void initialActivationUsesNativeProfileWithoutSoftwareFacts() {
        asset(1, "NORMAL", "PASSED", 9L);
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        assertThat(configurationEnvelope(1)).contains("UART_V2_SIMPLIFIED");
        assertThat(jdbc.queryForObject("SELECT weight_measurement_timeout_ms FROM dev_config_version", Long.class))
                .isEqualTo(5_000L);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_port_config_snapshot WHERE config_version_id=1 AND weight_required_sample_count=5 AND weight_maximum_fluctuation_g=100", Integer.class)).isEqualTo(2);
    }

    @ParameterizedTest
    @ValueSource(strings = {"unknown", "package", "version", "sequence", "release", "major", "minor", "asset", "stale", "notReady"})
    void softwareRecognitionDoesNotGateNativeProfile(String mismatch) {
        asset(1, "NORMAL", "PASSED", 9L);
        recognizedNativeSoftware(1, "BASE_COMPATIBLE");
        switch (mismatch) {
            case "unknown" -> jdbc.update("UPDATE dev_device_compatibility_projection SET compatibility_status='UNKNOWN'");
            case "package" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_package_sha256=?", new byte[32]);
            case "version" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_version_name='other'");
            case "sequence" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_release_sequence=99");
            case "release" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_release_uid='other'");
            case "major" -> jdbc.update("UPDATE dev_device_software_fact SET uart_protocol_major=1");
            case "minor" -> jdbc.update("UPDATE dev_device_software_fact SET uart_protocol_minor=9");
            case "asset" -> jdbc.update("UPDATE dev_device_software_fact SET asset_id=2");
            case "stale" -> jdbc.update("UPDATE dev_device_software_fact SET management_state_sequence=99");
            case "notReady" -> jdbc.update("UPDATE dev_device_software_fact SET uart_state='NEGOTIATING'");
        }
        tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID()));
        assertThat(configurationEnvelope(1)).contains("UART_V2_SIMPLIFIED");
        assertThat(jdbc.queryForObject("SELECT weight_measurement_timeout_ms FROM dev_config_version", Long.class)).isEqualTo(5_000L);
    }

    private void recognizedNativeSoftware(long assetId, String compatibility) {
        byte[] hash = HexFormat.of().parseHex("a1".repeat(32));
        jdbc.update("INSERT INTO dev_edge_software_release VALUES ('release-native', 2, 'native-2', ?, 'ECOBIN_UART', 2, 0, NULL)", hash);
        jdbc.update("INSERT INTO dev_device_software_fact VALUES (11, ?, 4, 'release-native', 2, 'native-2', ?, 'READY', 'ECOBIN_UART', 2, 0, NULL)", assetId, hash);
        jdbc.update("INSERT INTO dev_device_compatibility_projection VALUES (?, 11, 4, ?)", assetId, compatibility);
    }

    private String configurationEnvelope(long version) {
        return jdbc.queryForObject("SELECT command.semantic_payload FROM dev_device_command command JOIN dev_config_application application ON application.id=command.config_application_id JOIN dev_config_version config ON config.id=application.config_version_id WHERE config.version_no=?", String.class, version);
    }

    private void asset(long id, String lifecycle, String acceptance, Long organization) {
        jdbc.update("INSERT INTO dev_device_asset VALUES (?, ?, ?, 'EC-M0', 2, 7, ?, ?, ?, 0)", id, "TEST-"+id, code(id), organization, acceptance, lifecycle);
        if (organization != null) {
            jdbc.update("INSERT INTO dev_port VALUES (?, ?, 7, ?, 1), (?, ?, 7, ?, 2)", id*10+1, id, organization, id*10+2, id, organization);
        }
    }

    private void assertConfiguration(long asset, long version, String mode, long grams, long policyVersion) {
        assertThat(jdbc.queryForObject("SELECT device_default_policy_version_no FROM dev_config_version WHERE asset_id=? AND version_no=?", Long.class, asset, version)).isEqualTo(policyVersion);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM dev_port_config_snapshot p JOIN dev_config_version c ON c.id=p.config_version_id WHERE c.asset_id=? AND c.version_no=? AND p.fullness_mode=? AND p.configured_full_weight_g=?", Long.class, asset, version, mode, grams)).isEqualTo(2);
    }
}
