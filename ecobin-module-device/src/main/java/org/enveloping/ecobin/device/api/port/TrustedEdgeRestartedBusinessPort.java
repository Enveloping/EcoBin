package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedEdgeRestartedWork;

/**
 * Lets the module owning a device-command target close its business records
 * after the trusted edge reports that a restart cancelled physical work.
 */
public interface TrustedEdgeRestartedBusinessPort {

    void abortRestartedWork(TrustedEdgeRestartedWork work);
}
