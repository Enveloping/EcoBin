package org.enveloping.ecobin.bootstrap.database.epoch;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * P0 首个目标数据库纪元的固定判定规则。
 *
 * <p>V1 的 description/script/checksum 是纪元身份，V1～V49 必须逐版成功。
 * 这些值不能由部署配置覆盖，否则错误库可以通过修改环境变量被伪装成目标库。</p>
 */
public final class P0DatabaseEpochPolicy {

    static final String REQUIRED_DATABASE_PRODUCT = "MySQL";
    static final int REQUIRED_DATABASE_MAJOR_VERSION = 8;
    static final int REQUIRED_DATABASE_MINOR_VERSION = 4;
    static final String REQUIRED_DATABASE_PRINCIPAL = "ecobin_app";
    static final String HISTORY_TABLE = "flyway_schema_history";
    static final int MINIMUM_MIGRATION_VERSION = 49;
    static final String V1_DESCRIPTION = "p0 epoch and iam core";
    static final String V1_SCRIPT = "V1__p0_epoch_and_iam_core.sql";
    static final int V1_CHECKSUM = 229072802;

    private P0DatabaseEpochPolicy() {
    }

    public static Verification verify(Snapshot snapshot) {
        Objects.requireNonNull(snapshot, "snapshot");

        if (!REQUIRED_DATABASE_PRODUCT.equals(snapshot.productName())
                || snapshot.productMajorVersion() != REQUIRED_DATABASE_MAJOR_VERSION
                || snapshot.productMinorVersion() != REQUIRED_DATABASE_MINOR_VERSION) {
            throw new DatabaseEpochException(
                    "database engine must be MySQL 8.4.x");
        }
        if (snapshot.catalog() == null || snapshot.catalog().isBlank()) {
            throw new DatabaseEpochException(
                    "database connection has no selected target catalog");
        }
        String principal = principalName(snapshot.currentPrincipal());
        if (!REQUIRED_DATABASE_PRINCIPAL.equals(principal)) {
            throw new DatabaseEpochException(
                    "runtime database principal must be ecobin_app");
        }
        if (!snapshot.historyTablePresent()) {
            throw new DatabaseEpochException(
                    "target Flyway history table is missing");
        }

        List<Migration> versioned = snapshot.migrations().stream()
                .filter(migration -> migration.version() != null)
                .toList();
        versioned.stream()
                .filter(migration -> !migration.success())
                .findFirst()
                .ifPresent(migration -> {
                    throw new DatabaseEpochException(
                            "Flyway history contains a failed migration at version "
                                    + migration.version());
                });

        Migration v1 = exactlyOne(versioned, "1");
        if (!V1_DESCRIPTION.equals(v1.description())
                || !"SQL".equals(v1.type())
                || !V1_SCRIPT.equals(v1.script())
                || v1.checksum() == null
                || v1.checksum() != V1_CHECKSUM) {
            throw new DatabaseEpochException(
                    "target V1 epoch marker description/script/checksum does not match");
        }

        for (int version = 2; version <= MINIMUM_MIGRATION_VERSION; version++) {
            exactlyOne(versioned, Integer.toString(version));
        }

        return new Verification(
                snapshot.catalog(),
                principal,
                MINIMUM_MIGRATION_VERSION,
                V1_CHECKSUM);
    }

    private static Migration exactlyOne(List<Migration> migrations, String version) {
        List<Migration> matches = new ArrayList<>();
        for (Migration migration : migrations) {
            if (version.equals(migration.version())) {
                matches.add(migration);
            }
        }
        if (matches.size() != 1) {
            throw new DatabaseEpochException(
                    "Flyway history must contain exactly one successful V"
                            + version + " record");
        }
        return matches.getFirst();
    }

    private static String principalName(String currentPrincipal) {
        if (currentPrincipal == null || currentPrincipal.isBlank()) {
            return "";
        }
        int separator = currentPrincipal.indexOf('@');
        String principal = separator < 0
                ? currentPrincipal
                : currentPrincipal.substring(0, separator);
        return principal.replace("'", "").replace("`", "").trim();
    }

    public record Snapshot(
            String productName,
            int productMajorVersion,
            int productMinorVersion,
            String currentPrincipal,
            String catalog,
            boolean historyTablePresent,
            List<Migration> migrations) {

        public Snapshot {
            migrations = List.copyOf(migrations);
        }
    }

    public record Migration(
            int installedRank,
            String version,
            String description,
            String type,
            String script,
            Integer checksum,
            boolean success) {
    }

    public record Verification(
            String catalog,
            String principal,
            int minimumVersion,
            int v1Checksum) {
    }
}
