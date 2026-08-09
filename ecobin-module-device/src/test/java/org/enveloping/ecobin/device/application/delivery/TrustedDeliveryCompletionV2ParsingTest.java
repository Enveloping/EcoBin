package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class TrustedDeliveryCompletionV2ParsingTest {

    private static final String HARDWARE_SN = "test-divice-1";

    @Test
    void parsesV2EventWithoutLegacyDeviceCode() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        JsonNode event = contractEvent(
                mapper,
                "delivery-complete.event.json");
        assertThat(event.has("deviceCode")).isFalse();

        TrustedDeliveryCompletionService service =
                new TrustedDeliveryCompletionService(
                        null, mapper, null, null, null, null, null);
        var fact = service.parse(normalizedPayload(mapper, event));

        assertThat(fact.hardwareSn()).isEqualTo(HARDWARE_SN);
        assertThat(fact.sessionUid()).isEqualTo(UUID.fromString(
                "30000000-0000-4000-8000-000000000001"));
        assertThat(fact.edgeEventSequence()).isEqualTo(1042L);
    }

    private static String normalizedPayload(
            ObjectMapper mapper,
            JsonNode event) {
        ObjectNode root = mapper.createObjectNode();
        ObjectNode trustedSource = root.putObject("trustedSource");
        trustedSource.put("productId", "test-product");
        trustedSource.put("deviceName", HARDWARE_SN);
        root.set("event", event);
        root.put("eventCanonicalSha256", "f".repeat(64));
        return mapper.writeValueAsString(root);
    }

    private static JsonNode contractEvent(
            ObjectMapper mapper,
            String fileName) throws Exception {
        return mapper.readTree(Files.readString(contractPath(
                "contracts/examples/onenet/" + fileName)));
    }

    private static Path contractPath(String relative) {
        Path workingDirectory = Path.of("")
                .toAbsolutePath()
                .normalize();
        Path repository = Files.isDirectory(
                workingDirectory.resolve("contracts"))
                ? workingDirectory
                : workingDirectory.getParent();
        return repository.resolve(relative).normalize();
    }
}
