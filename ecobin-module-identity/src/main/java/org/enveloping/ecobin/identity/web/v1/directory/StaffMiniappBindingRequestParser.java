package org.enveloping.ecobin.identity.web.v1.directory;

import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.BindingSnapshot;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.SetStaffMiniappBindingRequest;
import tools.jackson.databind.JsonNode;

import java.util.UUID;

final class StaffMiniappBindingRequestParser {

    private StaffMiniappBindingRequestParser() {
    }

    static SetStaffMiniappBindingRequest parse(JsonNode body) {
        if (body == null
                || !body.isObject()
                || !body.hasNonNull("organizationUserUid")
                || !body.has("expectedStaffBinding")
                || !body.has("expectedOrganizationUserBinding")) {
            throw new IllegalArgumentException(
                    "both expected binding snapshots are required");
        }
        String reason = null;
        if (body.hasNonNull("reason")) {
            if (!body.path("reason").isTextual()
                    || body.path("reason").asText().length() > 500) {
                throw new IllegalArgumentException("invalid reason");
            }
            reason = body.path("reason").asText();
        }
        return new SetStaffMiniappBindingRequest(
                uuid(body.path("organizationUserUid")),
                snapshot(body.get("expectedStaffBinding")),
                snapshot(body.get("expectedOrganizationUserBinding")),
                reason);
    }

    private static BindingSnapshot snapshot(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        if (!node.isObject()
                || !node.hasNonNull("bindingUid")
                || !node.hasNonNull("version")
                || !node.path("version").isIntegralNumber()) {
            throw new IllegalArgumentException("invalid binding snapshot");
        }
        long version = node.path("version").asLong(-1);
        if (version < 0) {
            throw new IllegalArgumentException("invalid binding version");
        }
        return new BindingSnapshot(
                uuid(node.path("bindingUid")), version);
    }

    private static UUID uuid(JsonNode node) {
        if (!node.isTextual()) {
            throw new IllegalArgumentException("invalid public identifier");
        }
        return UUID.fromString(node.asText());
    }
}
