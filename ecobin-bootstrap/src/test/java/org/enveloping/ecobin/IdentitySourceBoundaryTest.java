package org.enveloping.ecobin;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class IdentitySourceBoundaryTest {

    @Test
    void otherModulesUseOnlyIdentityPublicApiAndLegacySystemPackageIsGone() throws IOException {
        Path root = repositoryRoot();
        List<String> violations = new ArrayList<>();

        for (Path file : javaSources(root)) {
            if (!isProductionSource(file)) {
                continue;
            }
            if (file.startsWith(root.resolve("ecobin-module-identity"))) {
                continue;
            }
            String source = Files.readString(file, StandardCharsets.UTF_8);
            if (source.contains("import org.enveloping.ecobin.system.")) {
                violations.add(root.relativize(file) + " imports removed system package");
            }
            if (source.matches("(?s).*org\\.enveloping\\.ecobin\\.identity\\."
                    + "(application|domain|infrastructure|web)\\..*")) {
                violations.add(root.relativize(file) + " imports identity internals");
            }
        }

        assertTrue(violations.isEmpty(), () -> String.join(System.lineSeparator(), violations));
        assertFalse(Files.exists(root.resolve("ecobin-module-system/pom.xml")));
    }

    @Test
    void persistenceReferenceHasOneIdentityOwnedIssuanceBridgeAndCallSite() throws IOException {
        Path root = repositoryRoot();
        List<Path> issuanceCallSites = new ArrayList<>();
        List<Path> bridgeImplementations = new ArrayList<>();

        for (Path file : javaSources(root)) {
            if (!isProductionSource(file)) {
                continue;
            }
            String source = Files.readString(file, StandardCharsets.UTF_8);
            if (source.contains("walletOwnerRefFactory.issue(")) {
                issuanceCallSites.add(root.relativize(file));
            }
            if (source.contains("implements OrganizationUserWalletOwnerRefFactory")) {
                bridgeImplementations.add(root.relativize(file));
            }
        }

        assertEquals(List.of(
                        Path.of(
                                "ecobin-module-identity/src/main/java/org/enveloping/ecobin/identity/"
                                        + "application/miniapp/TargetMiniappLoginTransactionService.java")),
                issuanceCallSites.stream().sorted().toList());
        assertEquals(List.of(Path.of(
                        "ecobin-module-identity/src/main/java/org/enveloping/ecobin/identity/"
                                + "api/persistence/"
                                + "IdentityOwnedOrganizationUserWalletOwnerRefFactory.java")),
                bridgeImplementations);
        assertFalse(Files.exists(root.resolve(
                "ecobin-module-identity/src/main/java/org/enveloping/ecobin/identity/"
                        + "api/persistence/OrganizationUserWalletOwnerRefIssuer.java")));
    }

    @Test
    void rawForeignKeysHaveOnlyTransactionParticipantsAsConsumers()
            throws IOException {
        Path root = repositoryRoot();
        List<Path> callSites = new ArrayList<>();

        for (Path file : javaSources(root)) {
            if (!isProductionSource(file)) {
                continue;
            }
            String source = Files.readString(file, StandardCharsets.UTF_8);
            if (source.contains(".writeForeignKeyTo(")) {
                callSites.add(root.relativize(file));
            }
        }

        assertEquals(List.of(
                        Path.of(
                                "ecobin-module-funds/src/main/java/org/enveloping/ecobin/funds/"
                                        + "infrastructure/registration/"
                                        + "JdbcOrganizationUserRegistrationParticipant.java"),
                        Path.of(
                                "ecobin-module-identity/src/main/java/org/enveloping/ecobin/identity/"
                                        + "application/miniapp/TargetMiniappLoginTransactionService.java"),
                        Path.of(
                                "ecobin-module-recycling/src/main/java/org/enveloping/ecobin/"
                                        + "recycling/infrastructure/registration/"
                                        + "JdbcOrganizationBootstrapParticipant.java")),
                callSites.stream().sorted().toList());
    }

    @Test
    void productionLegacySourceTreesAreGone() throws IOException {
        Path root = repositoryRoot();
        List<Path> violations = javaSources(root).stream()
                .filter(IdentitySourceBoundaryTest::isProductionSource)
                .map(root::relativize)
                .filter(path -> {
                    String normalized = path.toString().replace('\\', '/');
                    return normalized.contains("/legacy/")
                            || path.getFileName().toString().startsWith("Legacy");
                })
                .sorted()
                .toList();

        assertTrue(
                violations.isEmpty(),
                () -> "legacy production sources remain: " + violations);
    }

    private static List<Path> javaSources(Path root) throws IOException {
        try (var paths = Files.walk(root)) {
            return paths.filter(Files::isRegularFile)
                    .filter(path -> path.toString().endsWith(".java"))
                    .toList();
        }
    }

    private static boolean isProductionSource(Path file) {
        return file.toString().contains(
                "src" + java.io.File.separator + "main" + java.io.File.separator + "java");
    }

    private static Path repositoryRoot() {
        Path current = Path.of(System.getProperty("user.dir")).toAbsolutePath();
        while (current != null) {
            if (Files.exists(current.resolve("ecobin-bootstrap"))
                    && Files.exists(current.resolve("ecobin-module-identity"))) {
                return current;
            }
            current = current.getParent();
        }
        throw new IllegalStateException("repository root not found");
    }
}
