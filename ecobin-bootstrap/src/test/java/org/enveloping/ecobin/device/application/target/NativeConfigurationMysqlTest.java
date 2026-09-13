package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.*;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** Actual MySQL SQL/JSON and configuration producers. Minimal parent fixtures, not full-schema
 * foreign-key/trigger, production-permission, remote receive-plane or deployment evidence. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_NATIVE_CONFIGURATION_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_native_config_test(?:\\?.*)?")
class NativeConfigurationMysqlTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private SingleConnectionDataSource source;
    private JdbcTemplate jdbc;
    private TransactionTemplate tx;
    private AutomaticDeviceActivationService activation;
    private TargetDeviceApplication app;
    private McuConfigurationProfileProvider profiles;
    private String schema;

    @BeforeEach
    void prepare() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_NATIVE_CONFIGURATION_MYSQL_URL"), "root", "");
        assertThat(connection.getCatalog()).isEqualTo("ecobin_native_config_test");
        assertThat(connection.getMetaData().getDatabaseProductVersion()).startsWith("8.4.");
        source = new SingleConnectionDataSource(connection, true);
        jdbc = new JdbcTemplate(source);
        schema = "ecobin_native_config_case_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.execute("CREATE DATABASE `" + schema + "`");
        connection.setCatalog(schema);
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        String fixture = Files.readString(root.resolve("ecobin-module-device/src/test/resources/device-policy-fixture.sql"))
                .replaceAll("(?m)^CREATE ALIAS[^\\n]*\\n", "")
                .replaceAll("\\bVARCHAR\\b(?!\\s*\\()", "VARCHAR(512)");
        for (String sql : fixture.split(";")) {
            if (!sql.isBlank()) jdbc.execute(sql);
        }
        tx = new TransactionTemplate(new DataSourceTransactionManager(source));
        var tasks = mock(ReliableDeviceTaskRegistrationPort.class);
        var refs = mock(DeviceCommandTaskRefFactory.class);
        when(refs.issue(anyLong(), anyLong(), anyLong(), anyLong())).thenReturn(mock(DeviceCommandTaskRef.class));
        doAnswer(call -> {
            ReliableDeviceTaskRegistration task = call.getArgument(0);
            jdbc.update("INSERT INTO ops_reliable_task VALUES (?, ?, ?, 'PENDING')",
                    task.taskType(), task.targetType(), task.targetStableKey());
            return null;
        }).when(tasks).register(any());
        var canonicalizer = new DeviceConfigurationCanonicalizer();
        var runtime = new RuntimeSnapshotPolicyProvider(jdbc);
        activation = new AutomaticDeviceActivationService(jdbc, mapper, canonicalizer, runtime,
                new InitialDeviceConfigurationFactory(new InitialDeviceConfigurationProperties()), tasks, refs);
        app = new TargetDeviceApplication(jdbc, null, null, mapper, activation, canonicalizer, runtime,
                tasks, mock(ReliableDeviceTaskStatusPort.class), mock(ReliableTaskWakePort.class), refs,
                "test-product", null, null);
        profiles = new McuConfigurationProfileProvider(jdbc, mapper);
        jdbc.update("INSERT INTO dev_device_asset VALUES (1, 'TEST-NATIVE-1', 'Dv_aaaaaaaaaaaaaaaaaaaaaaaa1', 'EC-M0', 2, 7, 9, 'PASSED', 'NORMAL', 0)");
        jdbc.update("INSERT INTO dev_port VALUES (11, 1, 7, 9, 1), (12, 1, 7, 9, 2)");
    }

    @AfterEach
    void close() {
        try {
            if (jdbc != null && schema != null) {
                assertThat(schema).matches("ecobin_native_config_case_[0-9a-f]{32}");
                jdbc.execute("DROP DATABASE `" + schema + "`");
            }
        } finally {
            if (source != null) source.destroy();
        }
    }

    @Test
    void actualProducersAndScannerUpgradeOncePreserveHistoryAndNeverDowngradeForTimeout() {
        initial();
        // A completed legacy configuration must not schedule its baseline before the upgrade.
        jdbc.update("UPDATE dev_config_application SET status='APPLIED'");
        String original = envelope(1);
        byte[] hash = jdbc.queryForObject("SELECT content_sha256 FROM dev_config_version WHERE version_no=1", byte[].class);
        recognizedNative();
        assertThat(changedAssets()).containsExactly(1L);
        reconcile();
        assertThat(changedAssets()).isEmpty();
        assertThat(envelope(1)).isEqualTo(original);
        assertThat(jdbc.queryForObject("SELECT content_sha256 FROM dev_config_version WHERE version_no=1", byte[].class)).containsExactly(hash);
        JsonNode latest = mapper.readTree(envelope(2));
        assertThat(latest.path("payload").path("mcuConfigurationProfile").asString()).isEqualTo("UART_V2_SIMPLIFIED");
        assertThat(latest.path("payload").path("config").size()).isEqualTo(3);
        assertThat(latest.path("payload").path("ports").get(0).size()).isEqualTo(19);
        assertThat(profiles.latestFrozen(1)).isEqualTo(McuConfigurationProfile.UART_V2_SIMPLIFIED);
        assertThat(jdbc.queryForObject("SELECT weight_measurement_timeout_ms FROM dev_config_version WHERE version_no=2", Long.class)).isEqualTo(5_000);
        reconcile();
        assertThat(versionCount()).isEqualTo(2);
        jdbc.update("UPDATE dev_device_compatibility_projection SET compatibility_status='UNKNOWN'");
        jdbc.update("UPDATE dev_device_software_fact SET uart_state='DISCONNECTED'");
        assertThat(changedAssets()).isEmpty();
        assertThat(profiles.forPublication(1)).isEqualTo(McuConfigurationProfile.UART_V2_SIMPLIFIED);
        reconcile();
        assertThat(versionCount()).isEqualTo(2);
    }

    @Test
    void recognizedNativeInitialConfigurationDoesNotNeedExistingAppliedConfiguration() {
        recognizedNative();
        initial();
        assertThat(profiles.latestFrozen(1)).isEqualTo(McuConfigurationProfile.UART_V2_SIMPLIFIED);
        assertThat(jdbc.queryForObject("SELECT status FROM dev_config_application", String.class)).isEqualTo("PENDING");
        assertThat(changedAssets()).isEmpty();
    }

    @Test
    void onlyRecognizedFixedFrameSwitchCanPublishANewLegacyProfile() {
        recognizedNative();
        initial();
        String nativeOriginal = envelope(1);
        jdbc.update("UPDATE dev_edge_software_release SET uart_protocol_family='FIXED_FRAME', uart_protocol_major=NULL, uart_protocol_minor=NULL, required_fixed_frame_revision=3");
        jdbc.update("UPDATE dev_device_software_fact SET uart_protocol_family='FIXED_FRAME', uart_protocol_major=NULL, uart_protocol_minor=NULL, mcu_fixed_frame_revision=3");
        assertThat(changedAssets()).containsExactly(1L);
        reconcile();
        assertThat(profiles.latestFrozen(1)).isEqualTo(McuConfigurationProfile.LEGACY_V1);
        assertThat(envelope(2)).doesNotContain("mcuConfigurationProfile");
        assertThat(envelope(1)).isEqualTo(nativeOriginal);
        assertThat(changedAssets()).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(strings = {"unknown", "hash", "version", "sequence", "uid", "major", "minor", "asset", "factSequence", "notReady", "disabled", "unassigned"})
    void unknownOrMismatchedSoftwareAndUnavailableAssetsCannotEnterProfileChangeScan(String mismatch) {
        initial();
        recognizedNative();
        switch (mismatch) {
            case "unknown" -> jdbc.update("UPDATE dev_device_compatibility_projection SET compatibility_status='UNKNOWN'");
            case "hash" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_package_sha256=?", new byte[32]);
            case "version" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_version_name='other'");
            case "sequence" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_release_sequence=9");
            case "uid" -> jdbc.update("UPDATE dev_device_software_fact SET active_business_release_uid='other'");
            case "major" -> jdbc.update("UPDATE dev_device_software_fact SET uart_protocol_major=1");
            case "minor" -> jdbc.update("UPDATE dev_device_software_fact SET uart_protocol_minor=9");
            case "asset" -> jdbc.update("UPDATE dev_device_software_fact SET asset_id=2");
            case "factSequence" -> jdbc.update("UPDATE dev_device_software_fact SET management_state_sequence=8");
            case "notReady" -> jdbc.update("UPDATE dev_device_software_fact SET uart_state='NEGOTIATING'");
            case "disabled" -> jdbc.update("UPDATE dev_device_asset SET lifecycle_status='DISABLED'");
            case "unassigned" -> jdbc.update("UPDATE dev_device_asset SET organization_id=NULL");
        }
        assertThat(changedAssets()).isEmpty();
        reconcile();
        assertThat(versionCount()).isEqualTo(1);
        assertThat(envelope(1)).doesNotContain("mcuConfigurationProfile");
    }

    private void recognizedNative() {
        byte[] hash = HexFormat.of().parseHex("a1".repeat(32));
        jdbc.update("INSERT INTO dev_edge_software_release VALUES ('release-native', 2, 'native-2', ?, 'ECOBIN_UART', 2, 0, NULL)", hash);
        jdbc.update("INSERT INTO dev_device_software_fact VALUES (11, 1, 4, 'release-native', 2, 'native-2', ?, 'READY', 'ECOBIN_UART', 2, 0, NULL)", hash);
        jdbc.update("INSERT INTO dev_device_compatibility_projection VALUES (1, 11, 4, 'BASE_COMPATIBLE')");
    }

    private void initial() { tx.executeWithoutResult(status -> activation.reconcileInCurrentTransaction(1, UUID.randomUUID())); }
    private void reconcile() { tx.executeWithoutResult(status -> app.reconcileAutomaticActivation(1)); }
    private int versionCount() { return jdbc.queryForObject("SELECT COUNT(*) FROM dev_config_version", Integer.class); }
    private List<Long> changedAssets() { return jdbc.queryForList(McuConfigurationProfileProvider.PROFILE_CHANGE_ASSET_IDS_SQL, Long.class); }
    private String envelope(long version) {
        return jdbc.queryForObject("SELECT command.semantic_payload FROM dev_device_command command JOIN dev_config_application application ON application.id=command.config_application_id JOIN dev_config_version config ON config.id=application.config_version_id WHERE config.version_no=?", String.class, version);
    }
}
