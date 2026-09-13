package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;
import java.util.Optional;

/** Called in the asset owner's transaction, after locking that asset. */
public interface ReliableDeviceTaskLifecyclePort {
    boolean externalCallMayHaveStarted(UUID taskUid);

    /** Cancel device work; historical result acknowledgements remain deliverable. */
    void cancelDeviceWork(String hardwareSn);

    /** Suspend configuration/update intents and cancel other device work. */
    void pauseDeviceWork(String hardwareSn);

    /** Restore dispatch gates without reopening failed or cancelled tasks. */
    void resumeDeviceWork(String hardwareSn);

    Optional<RenewedUpgradeCommand> renewExpiredUnsentUpgradeTask(String hardwareSn, UUID taskUid);

    record RenewedUpgradeCommand(UUID taskUid, UUID commandUid) { }
}
