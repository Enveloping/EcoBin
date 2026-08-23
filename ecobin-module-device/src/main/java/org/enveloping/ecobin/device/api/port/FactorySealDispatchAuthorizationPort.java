package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.FactorySealDispatchDecision;

import java.time.LocalDateTime;
import java.util.UUID;

/** Rechecks the exact acceptance and bag snapshot immediately before dispatch. */
public interface FactorySealDispatchAuthorizationPort {

    FactorySealDispatchDecision authorizeDispatch(
            UUID reliableTaskUid,
            UUID commandUid,
            String hardwareSn,
            LocalDateTime checkedAt);
}
