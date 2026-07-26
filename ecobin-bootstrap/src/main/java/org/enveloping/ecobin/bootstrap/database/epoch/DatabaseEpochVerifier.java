package org.enveloping.ecobin.bootstrap.database.epoch;

import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

/**
 * 使用运行数据源执行只读纪元检查。
 */
@Component
public final class DatabaseEpochVerifier {

    private static final String HISTORY_EXISTS_SQL = """
            SELECT COUNT(*)
              FROM information_schema.tables
             WHERE table_schema = ?
               AND table_name = 'flyway_schema_history'
               AND table_type = 'BASE TABLE'
            """;
    private static final String HISTORY_SQL = """
            SELECT installed_rank, version, description, type, script, checksum, success
              FROM flyway_schema_history
             ORDER BY installed_rank
            """;

    private final DataSource dataSource;

    public DatabaseEpochVerifier(DataSource dataSource) {
        this.dataSource = dataSource;
    }

    public P0DatabaseEpochPolicy.Verification verify() {
        return P0DatabaseEpochPolicy.verify(readSnapshot());
    }

    public boolean isEmbeddedH2() {
        try (Connection connection = dataSource.getConnection()) {
            DatabaseMetaData metadata = connection.getMetaData();
            return "H2".equals(metadata.getDatabaseProductName())
                    && metadata.getURL() != null
                    && metadata.getURL().startsWith("jdbc:h2:mem:");
        } catch (SQLException exception) {
            throw new DatabaseEpochException(
                    "cannot inspect test database for epoch bypass", exception);
        }
    }

    P0DatabaseEpochPolicy.Snapshot readSnapshot() {
        try (Connection connection = dataSource.getConnection()) {
            connection.setReadOnly(true);
            DatabaseMetaData metadata = connection.getMetaData();
            String catalog = connection.getCatalog();
            String currentPrincipal = readCurrentPrincipal(connection);
            boolean historyPresent = historyTablePresent(connection, catalog);
            List<P0DatabaseEpochPolicy.Migration> migrations = historyPresent
                    ? readMigrations(connection)
                    : List.of();

            return new P0DatabaseEpochPolicy.Snapshot(
                    metadata.getDatabaseProductName(),
                    metadata.getDatabaseMajorVersion(),
                    metadata.getDatabaseMinorVersion(),
                    currentPrincipal,
                    catalog,
                    historyPresent,
                    migrations);
        } catch (SQLException exception) {
            throw new DatabaseEpochException(
                    "cannot read target database epoch", exception);
        }
    }

    private static String readCurrentPrincipal(Connection connection)
            throws SQLException {
        try (Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery("SELECT CURRENT_USER()")) {
            if (!result.next()) {
                throw new DatabaseEpochException(
                        "database did not return the current principal");
            }
            return result.getString(1);
        }
    }

    private static boolean historyTablePresent(
            Connection connection,
            String catalog) throws SQLException {
        try (PreparedStatement statement =
                     connection.prepareStatement(HISTORY_EXISTS_SQL)) {
            statement.setString(1, catalog);
            try (ResultSet result = statement.executeQuery()) {
                return result.next() && result.getInt(1) == 1;
            }
        }
    }

    private static List<P0DatabaseEpochPolicy.Migration> readMigrations(
            Connection connection) throws SQLException {
        List<P0DatabaseEpochPolicy.Migration> migrations = new ArrayList<>();
        try (Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery(HISTORY_SQL)) {
            while (result.next()) {
                int checksumValue = result.getInt("checksum");
                Integer checksum = result.wasNull() ? null : checksumValue;
                migrations.add(new P0DatabaseEpochPolicy.Migration(
                        result.getInt("installed_rank"),
                        result.getString("version"),
                        result.getString("description"),
                        result.getString("type"),
                        result.getString("script"),
                        checksum,
                        result.getBoolean("success")));
            }
        }
        return migrations;
    }
}
