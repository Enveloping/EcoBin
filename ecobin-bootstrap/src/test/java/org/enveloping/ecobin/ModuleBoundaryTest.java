package org.enveloping.ecobin;

import org.junit.jupiter.api.Test;

import javax.xml.parsers.DocumentBuilderFactory;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ModuleBoundaryTest {

    private static final Map<String, String> BUSINESS_MODULE_PACKAGES =
            new LinkedHashMap<>();
    private static final Map<String, Set<String>> EXPECTED_INTERNAL_DEPENDENCIES =
            Map.of(
                    "ecobin-framework", Set.of(),
                    "ecobin-module-identity", Set.of("ecobin-framework"),
                    "ecobin-module-device", Set.of(
                            "ecobin-framework", "ecobin-module-identity"),
                    "ecobin-module-funds", Set.of(
                            "ecobin-framework", "ecobin-module-identity"),
                    "ecobin-module-recycling", Set.of(
                            "ecobin-framework",
                            "ecobin-module-identity",
                            "ecobin-module-device",
                            "ecobin-module-funds"),
                    "ecobin-module-operations", Set.of(
                            "ecobin-framework",
                            "ecobin-module-identity",
                            "ecobin-module-device",
                            "ecobin-module-funds",
                            "ecobin-module-recycling"),
                    "ecobin-integration", Set.of(
                            "ecobin-framework",
                            "ecobin-module-identity",
                            "ecobin-module-device",
                            "ecobin-module-funds",
                            "ecobin-module-recycling",
                            "ecobin-module-operations"),
                    "ecobin-bootstrap", Set.of(
                            "ecobin-framework",
                            "ecobin-module-identity",
                            "ecobin-module-device",
                            "ecobin-module-funds",
                            "ecobin-module-recycling",
                            "ecobin-module-operations",
                            "ecobin-integration"));

    static {
        BUSINESS_MODULE_PACKAGES.put(
                "ecobin-module-identity", "org.enveloping.ecobin.identity");
        BUSINESS_MODULE_PACKAGES.put(
                "ecobin-module-device", "org.enveloping.ecobin.device");
        BUSINESS_MODULE_PACKAGES.put(
                "ecobin-module-funds", "org.enveloping.ecobin.funds");
        BUSINESS_MODULE_PACKAGES.put(
                "ecobin-module-recycling", "org.enveloping.ecobin.recycling");
        BUSINESS_MODULE_PACKAGES.put(
                "ecobin-module-operations", "org.enveloping.ecobin.operations");
    }

    @Test
    void rootReactorContainsOnlyActiveBackendModules() throws Exception {
        Path root = repositoryRoot();
        var document = DocumentBuilderFactory.newInstance()
                .newDocumentBuilder()
                .parse(root.resolve("pom.xml").toFile());
        var moduleNodes = document.getElementsByTagName("module");
        List<String> modules = new ArrayList<>();
        for (int i = 0; i < moduleNodes.getLength(); i++) {
            modules.add(moduleNodes.item(i).getTextContent().trim());
        }
        String rootPom = Files.readString(root.resolve("pom.xml"), StandardCharsets.UTF_8);

        assertEquals(List.of(
                "ecobin-framework",
                "ecobin-module-identity",
                "ecobin-module-device",
                "ecobin-module-funds",
                "ecobin-module-recycling",
                "ecobin-module-operations",
                "ecobin-integration",
                "ecobin-bootstrap"), modules);
        assertFalse(Files.exists(root.resolve("ecobin-module-system/pom.xml")));
        assertFalse(Files.exists(root.resolve("ecobin-module-business/pom.xml")));
        assertFalse(rootPom.contains("ecobin-module-system"));
        assertFalse(rootPom.contains("ecobin-module-business"));
    }

    @Test
    void internalMavenDependenciesFollowFrozenDag() throws Exception {
        Path root = repositoryRoot();

        for (Map.Entry<String, Set<String>> module : EXPECTED_INTERNAL_DEPENDENCIES.entrySet()) {
            var document = DocumentBuilderFactory.newInstance()
                    .newDocumentBuilder()
                    .parse(root.resolve(module.getKey()).resolve("pom.xml").toFile());
            var dependencyNodes = document.getElementsByTagName("dependency");
            Set<String> actual = new java.util.HashSet<>();

            for (int i = 0; i < dependencyNodes.getLength(); i++) {
                var dependency = dependencyNodes.item(i);
                String groupId = childText(dependency, "groupId");
                String artifactId = childText(dependency, "artifactId");
                if ("org.enveloping".equals(groupId) && artifactId != null) {
                    actual.add(artifactId);
                }
            }

            assertEquals(
                    module.getValue(),
                    actual,
                    () -> module.getKey() + " has unexpected internal Maven dependencies");
        }
    }

    @Test
    void productionModulesImportOtherBusinessModulesOnlyThroughApi() throws IOException {
        Path root = repositoryRoot();
        List<String> violations = new ArrayList<>();

        for (Path file : productionJavaSources(root)) {
            String source = Files.readString(file, StandardCharsets.UTF_8);
            String ownerModule = ownerModule(root, file);
            Path relative = root.relativize(file);

            if (source.contains("org.enveloping.ecobin.business.")) {
                violations.add(relative + " references removed business package");
            }

            for (Map.Entry<String, String> module : BUSINESS_MODULE_PACKAGES.entrySet()) {
                if (module.getKey().equals(ownerModule)) {
                    continue;
                }
                String prefix = module.getValue() + ".";
                for (String line : source.lines().toList()) {
                    String trimmed = line.trim();
                    if (trimmed.startsWith("import " + prefix)
                            && !trimmed.startsWith("import " + prefix + "api.")) {
                        violations.add(relative + " imports " + trimmed);
                    }
                }
                if (source.contains(prefix + "entity.")
                        || source.contains(prefix + "mapper.")
                        || source.contains(prefix + "repository.")
                        || source.contains(prefix + "service.")) {
                    violations.add(relative + " references private package under " + prefix);
                }
            }
        }

        assertTrue(violations.isEmpty(), () -> String.join(System.lineSeparator(), violations));
    }

    @Test
    void bootstrapContainsAssemblyOnly() throws IOException {
        Path root = repositoryRoot();
        Path mainJava = root.resolve("ecobin-bootstrap/src/main/java");
        Set<String> forbidden = Set.of(
                "@RestController", "@Service", "@Repository", "@Mapper",
                "package org.enveloping.ecobin.bootstrap.application",
                "package org.enveloping.ecobin.bootstrap.domain");
        List<String> violations = new ArrayList<>();

        try (var files = Files.walk(mainJava)) {
            for (Path file : files.filter(Files::isRegularFile)
                    .filter(path -> path.toString().endsWith(".java"))
                    .toList()) {
                String source = Files.readString(file, StandardCharsets.UTF_8);
                for (String marker : forbidden) {
                    if (source.contains(marker)) {
                        violations.add(root.relativize(file) + " contains " + marker);
                    }
                }
            }
        }
        assertTrue(violations.isEmpty(), () -> String.join(System.lineSeparator(), violations));
    }

    private static List<Path> productionJavaSources(Path root) throws IOException {
        try (var files = Files.walk(root)) {
            return files.filter(Files::isRegularFile)
                    .filter(path -> path.toString().endsWith(".java"))
                    .filter(path -> path.toString().contains(
                            "src" + java.io.File.separator
                                    + "main" + java.io.File.separator
                                    + "java"))
                    .toList();
        }
    }

    private static String ownerModule(Path root, Path file) {
        Path relative = root.relativize(file);
        return relative.getNameCount() == 0 ? "" : relative.getName(0).toString();
    }

    private static String childText(org.w3c.dom.Node parent, String childName) {
        for (int i = 0; i < parent.getChildNodes().getLength(); i++) {
            var child = parent.getChildNodes().item(i);
            if (childName.equals(child.getNodeName())) {
                return child.getTextContent().trim();
            }
        }
        return null;
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
