package org.enveloping.ecobin.recycling.application.deliveryquery;

import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Set;

/**
 * Current recycling-owned facts needed to render delivery options.
 */
public record DeliveryOptionsBusinessFacts(
        OptionalLong openBalanceFloorCent,
        List<DeliveryPortBusinessFacts> ports) {

    public DeliveryOptionsBusinessFacts {
        Objects.requireNonNull(
                openBalanceFloorCent,
                "openBalanceFloorCent");
        ports = List.copyOf(ports);
        Set<Integer> portNumbers = new HashSet<>();
        for (DeliveryPortBusinessFacts port : ports) {
            Objects.requireNonNull(port, "ports must not contain null");
            if (!portNumbers.add(port.portNo())) {
                throw new IllegalArgumentException(
                        "duplicate portNo: " + port.portNo());
            }
        }
    }

    /**
     * Empty means the organization has no usable current delivery
     * configuration; it is not a synthetic zero threshold.
     */
    public boolean deliveryConfigurationPresent() {
        return openBalanceFloorCent.isPresent();
    }

    public Optional<DeliveryPortBusinessFacts> port(int portNo) {
        return ports.stream()
                .filter(port -> port.portNo() == portNo)
                .findFirst();
    }
}
