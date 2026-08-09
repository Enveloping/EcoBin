package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;

import java.util.Objects;

/** 已认证、规范化并以平台资产作用域可靠收件的设备事实。 */
public record TrustedPlatformDeviceAssetFactEvent(
        TrustedPlatformInboxRef sourceInbox,
        String messageKind,
        int normalizedSchemaVersion,
        String normalizedPayload) {

    public TrustedPlatformDeviceAssetFactEvent {
        Objects.requireNonNull(sourceInbox, "sourceInbox");
        Objects.requireNonNull(messageKind, "messageKind");
        Objects.requireNonNull(normalizedPayload, "normalizedPayload");
        if (normalizedSchemaVersion != 2) {
            throw new IllegalArgumentException(
                    "unsupported platform device fact schema version");
        }
    }
}
