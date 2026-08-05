package org.enveloping.ecobin.identity.api.result;

import java.util.Map;
import java.util.UUID;

public record BagTraceIdentityFacts(Map<UUID, User> users) {
    public BagTraceIdentityFacts {
        users = Map.copyOf(users);
    }

    public record User(
            UUID organizationUserUid,
            String nickname,
            String maskedPhoneNumber) {
    }
}
