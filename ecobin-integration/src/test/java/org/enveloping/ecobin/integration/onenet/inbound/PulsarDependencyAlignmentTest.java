package org.enveloping.ecobin.integration.onenet.inbound;

import org.apache.pulsar.client.api.PulsarClient;
import org.apache.pulsar.client.impl.PulsarClientImpl;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class PulsarDependencyAlignmentTest {

    @Test
    void clientImplementationAndApiUseTheSameVersion() {
        assertEquals(
                implementationVersion(PulsarClient.class),
                implementationVersion(PulsarClientImpl.class),
                "Pulsar implementation and API artifacts must remain version-aligned");
    }

    private static String implementationVersion(Class<?> type) {
        String version = type.getPackage().getImplementationVersion();
        assertNotNull(version, () -> "Missing implementation version for " + type.getName());
        return version;
    }
}
