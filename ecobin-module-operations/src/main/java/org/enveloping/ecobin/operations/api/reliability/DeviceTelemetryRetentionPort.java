package org.enveloping.ecobin.operations.api.reliability;

import java.time.Instant;

public interface DeviceTelemetryRetentionPort {

    int purgeRuntimeSnapshotsBefore(Instant cutoff, int batchSize);
}
