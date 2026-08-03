package org.enveloping.ecobin.device.api.port;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Closes physical work whose authorization expired while its reliable task
 * was still held before dispatch by an absent OneNet presence fact.
 *
 * <p>The returned values are the internal device-command ids whose reliable
 * tasks must be cancelled in the same database transaction.</p>
 */
public interface ExpiredUnstartedDeviceWorkPort {

    List<Long> closeExpiredUnstartedWork(LocalDateTime now);
}
