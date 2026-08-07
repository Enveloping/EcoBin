package org.enveloping.ecobin.device.api.result;

import java.util.Map;

public record DeviceOperationalOverview(
        Map<String, Long> onlineByOrganization,
        Map<String, java.util.List<RegistrationAttribution>>
                registrationAttributions) {
    public DeviceOperationalOverview {
        onlineByOrganization = Map.copyOf(onlineByOrganization);
        registrationAttributions = registrationAttributions.entrySet().stream()
                .collect(java.util.stream.Collectors.toUnmodifiableMap(
                        Map.Entry::getKey,
                        entry -> java.util.List.copyOf(entry.getValue())));
    }

    public record RegistrationAttribution(
            String deviceCode,
            String deviceName,
            long registeredUserCount) { }
}
