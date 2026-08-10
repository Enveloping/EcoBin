package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

public record ReliableDeviceTaskStatus(
        UUID taskUid,
        String state,
        String blockedReasonCode,
        long wakeVersion,
        long lockVersion) {
}
