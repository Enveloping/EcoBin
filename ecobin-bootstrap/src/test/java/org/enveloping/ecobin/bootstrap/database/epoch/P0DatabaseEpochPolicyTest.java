package org.enveloping.ecobin.bootstrap.database.epoch;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class P0DatabaseEpochPolicyTest {

    @Test
    void acceptsTheExactV1MarkerAndCompleteV81Epoch() {
        var verification = P0DatabaseEpochPolicy.verify(validSnapshot());

        assertEquals("ecobin_target", verification.catalog());
        assertEquals("ecobin_app", verification.principal());
        assertEquals(81, verification.minimumVersion());
        assertEquals(229072802, verification.v1Checksum());
    }

    @Test
    void rejectsAnEmptySchema() {
        assertRejected(snapshot(false, validMigrations()));
    }

    @Test
    void rejectsLegacyOrWrongV1() {
        List<P0DatabaseEpochPolicy.Migration> migrations =
                new ArrayList<>(validMigrations());
        migrations.set(0, migration(
                1, "1", "init schema", "V1__init_schema.sql", -123, true));

        assertRejected(snapshot(true, migrations));
    }

    @Test
    void rejectsV1ChecksumDrift() {
        List<P0DatabaseEpochPolicy.Migration> migrations =
                new ArrayList<>(validMigrations());
        migrations.set(0, migration(
                1, "1", "p0 epoch and iam core",
                "V1__p0_epoch_and_iam_core.sql", 123, true));

        assertRejected(snapshot(true, migrations));
    }

    @Test
    void rejectsAFailedMigration() {
        List<P0DatabaseEpochPolicy.Migration> migrations =
                new ArrayList<>(validMigrations());
        migrations.set(6, migration(
                7, "7", "operations", "V7__operations.sql", 7, false));

        assertRejected(snapshot(true, migrations));
    }

    @Test
    void rejectsAnEpochBelowV81() {
        List<P0DatabaseEpochPolicy.Migration> migrations =
                new ArrayList<>(validMigrations());
        migrations.removeLast();

        assertRejected(snapshot(true, migrations));
    }

    @Test
    void rejectsAMissingIntermediateVersion() {
        List<P0DatabaseEpochPolicy.Migration> migrations =
                new ArrayList<>(validMigrations());
        migrations.remove(4);

        assertRejected(snapshot(true, migrations));
    }

    @Test
    void rejectsDuplicateVersionRows() {
        List<P0DatabaseEpochPolicy.Migration> migrations =
                new ArrayList<>(validMigrations());
        migrations.add(migration(
                11, "10", "duplicate", "V10__duplicate.sql", 10, true));

        assertRejected(snapshot(true, migrations));
    }

    @Test
    void rejectsSchemaOwnerAndTriggerDefinerRuntimeCredentials() {
        var ownerSnapshot = new P0DatabaseEpochPolicy.Snapshot(
                "MySQL", 8, 4, "ecobin_schema_owner@%",
                "ecobin_target", true, validMigrations());
        var definerSnapshot = new P0DatabaseEpochPolicy.Snapshot(
                "MySQL", 8, 4, "ecobin_trigger_definer@%",
                "ecobin_target", true, validMigrations());

        assertRejected(ownerSnapshot);
        assertRejected(definerSnapshot);
    }

    @Test
    void rejectsNonTargetDatabaseEngineVersion() {
        var snapshot = new P0DatabaseEpochPolicy.Snapshot(
                "MySQL", 8, 0, "ecobin_app@%",
                "ecobin_target", true, validMigrations());

        assertRejected(snapshot);
    }

    private static P0DatabaseEpochPolicy.Snapshot validSnapshot() {
        return snapshot(true, validMigrations());
    }

    private static P0DatabaseEpochPolicy.Snapshot snapshot(
            boolean historyPresent,
            List<P0DatabaseEpochPolicy.Migration> migrations) {
        return new P0DatabaseEpochPolicy.Snapshot(
                "MySQL",
                8,
                4,
                "ecobin_app@%",
                "ecobin_target",
                historyPresent,
                migrations);
    }

    private static List<P0DatabaseEpochPolicy.Migration> validMigrations() {
        List<P0DatabaseEpochPolicy.Migration> migrations = new ArrayList<>();
        migrations.add(migration(
                1,
                "1",
                "p0 epoch and iam core",
                "V1__p0_epoch_and_iam_core.sql",
                229072802,
                true));
        for (int version = 2; version <= 81; version++) {
            migrations.add(migration(
                    version,
                    Integer.toString(version),
                    "migration " + version,
                    "V" + version + "__migration.sql",
                    version,
                    true));
        }
        return migrations;
    }

    private static P0DatabaseEpochPolicy.Migration migration(
            int installedRank,
            String version,
            String description,
            String script,
            int checksum,
            boolean success) {
        return new P0DatabaseEpochPolicy.Migration(
                installedRank,
                version,
                description,
                "SQL",
                script,
                checksum,
                success);
    }

    private static void assertRejected(
            P0DatabaseEpochPolicy.Snapshot snapshot) {
        assertThrows(
                DatabaseEpochException.class,
                () -> P0DatabaseEpochPolicy.verify(snapshot));
    }
}
