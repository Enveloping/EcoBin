package org.enveloping.ecobin.framework.idempotency;

import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GlobalOperationIdempotencyApiTest {

    @Test
    void exposesTheExistingLengthFramedCanonicalDigest() {
        assertThat(GlobalOperationDigests.sha256("ab", "c"))
                .isEqualTo(GlobalOperationDigests.sha256("ab", "c"))
                .isNotEqualTo(GlobalOperationDigests.sha256("a", "bc"));
        assertThat(GlobalOperationDigests.platformScope())
                .isEqualTo("f50d89599238101345f2e0d7ca6d66a5"
                        + "ee366fde7c3218b5ce786be2e66d71ea")
                .isEqualTo(GlobalOperationDigests.sha256(
                        "PLATFORM", "", false));
    }

    @Test
    void bindingCarriesTheCompleteGlobalOperationIdentity() {
        UUID operationUid = UUID.randomUUID();
        UUID actorUid = UUID.randomUUID();
        String scope = GlobalOperationDigests.platformScope();
        String request = GlobalOperationDigests.sha256("request");

        GlobalOperationBinding binding = new GlobalOperationBinding(
                operationUid,
                "PLATFORM_ADMIN",
                actorUid,
                scope,
                "device.remote-support.open",
                "remote-support-session",
                "SN-001",
                request);

        assertThat(binding.operationUid()).isEqualTo(operationUid);
        assertThat(binding.actorUid()).isEqualTo(actorUid);
        assertThat(binding.scopeDigest()).isEqualTo(scope);
        assertThat(binding.requestDigest()).isEqualTo(request);
    }

    @Test
    void bindingRejectsMissingIdentityAndNonCanonicalDigests() {
        UUID operationUid = UUID.randomUUID();
        UUID actorUid = UUID.randomUUID();
        String digest = "11".repeat(32);

        assertThatThrownBy(() -> new GlobalOperationBinding(
                null, "PLATFORM_ADMIN", actorUid, digest,
                "action", "target", "key", digest))
                .isInstanceOf(NullPointerException.class)
                .hasMessage("operationUid");
        assertThatThrownBy(() -> new GlobalOperationBinding(
                operationUid, "PLATFORM_ADMIN", null, digest,
                "action", "target", "key", digest))
                .isInstanceOf(NullPointerException.class)
                .hasMessage("actorUid");
        assertThatThrownBy(() -> new GlobalOperationBinding(
                operationUid, "PLATFORM_ADMIN", actorUid, "AA".repeat(32),
                "action", "target", "key", digest))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scopeDigest");
        assertThatThrownBy(() -> new GlobalOperationBinding(
                operationUid, "PLATFORM_ADMIN", actorUid, digest,
                "action", "target", "key", "11".repeat(31)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requestDigest");
    }

    @Test
    void completedResultRequiresStableReplayFields() {
        assertThatThrownBy(() -> new GlobalOperationResult(
                null, "SUCCEEDED", 1))
                .isInstanceOf(NullPointerException.class)
                .hasMessage("resourceUid");
        assertThatThrownBy(() -> new GlobalOperationResult(
                UUID.randomUUID(), " ", 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("state");
    }
}
