package org.enveloping.ecobin.device.api.port;

import java.util.UUID;

/** Refreshes an expired authorization only before any external submission. */
public interface UnsentUpgradeAuthorizationPort {
    boolean refreshBeforeDispatch(String hardwareSn, UUID taskUid);
}
