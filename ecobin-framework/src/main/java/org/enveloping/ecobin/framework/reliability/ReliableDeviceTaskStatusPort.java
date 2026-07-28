package org.enveloping.ecobin.framework.reliability;

import java.util.Optional;

/**
 * Safe query Seam for business Modules that own device intent but not the
 * operations task tables.
 */
public interface ReliableDeviceTaskStatusPort {

    Optional<ReliableDeviceTaskStatus> find(
            String taskType,
            String targetType,
            String targetStableKey,
            boolean forUpdate);
}
