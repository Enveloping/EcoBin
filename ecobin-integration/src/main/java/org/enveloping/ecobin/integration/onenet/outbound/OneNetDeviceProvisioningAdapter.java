package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningException;
import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningPort;
import org.enveloping.ecobin.device.api.result.OneNetProvisionedDevice;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.util.Map;
import java.util.Set;

/** Real OneNet device create/detail adapter with ambiguity recovery. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
public class OneNetDeviceProvisioningAdapter
        implements OneNetDeviceProvisioningPort {

    private static final Set<String> NOT_FOUND_CODES = Set.of(
            "10014", "10410", "10411", "DEVICE_NOT_FOUND");
    private static final Set<String> RETRYABLE_CODES = Set.of(
            "10500", "INTERNAL_ERROR", "SERVICE_BUSY");

    private final OneNetProperties properties;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public OneNetDeviceProvisioningAdapter(
            OneNetProperties properties,
            RestTemplate restTemplate,
            ObjectMapper objectMapper) {
        this.properties = properties;
        this.restTemplate = restTemplate;
        this.objectMapper = objectMapper;
    }

    @Override
    public OneNetProvisionedDevice ensureDevice(
            String deviceName,
            String descriptionMarker,
            boolean createIfMissing) {
        requireConfigured();
        Lookup lookup = detail(deviceName);
        if (lookup.device() != null) {
            return requireMarker(lookup.device(), descriptionMarker,
                    createIfMissing);
        }
        if (!lookup.missing()) {
            throw lookup.failure();
        }
        if (!createIfMissing) {
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_DEVICE_NOT_FOUND",
                    "OneNet device does not exist");
        }
        return createOrRecover(deviceName, descriptionMarker);
    }

    private OneNetProvisionedDevice createOrRecover(
            String deviceName,
            String descriptionMarker) {
        Map<String, Object> body = Map.of(
                "product_id", properties.getProductId(),
                "device_name", deviceName,
                "desc", descriptionMarker);
        try {
            ResponseEntity<String> response = restTemplate.postForEntity(
                    properties.getBaseUrl()
                            + properties.getCreateDevicePath(),
                    new HttpEntity<>(body, headers()),
                    String.class);
            JsonNode envelope = parse(response.getBody());
            String code = code(envelope);
            if ("0".equals(code)) {
                return requireMarker(device(envelope.path("data")),
                        descriptionMarker, true);
            }

            // A timeout or a duplicate-name reply can both mean the first
            // create committed.  Query before deciding the operation failed.
            Lookup recovered = detail(deviceName);
            if (recovered.device() != null) {
                return requireMarker(
                        recovered.device(), descriptionMarker, true);
            }
            if (RETRYABLE_CODES.contains(code)) {
                throw OneNetDeviceProvisioningException.retryable(
                        "ONENET_CREATE_RETRYABLE",
                        "OneNet temporarily rejected device creation",
                        null);
            }
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_CREATE_REJECTED",
                    "OneNet rejected device creation");
        } catch (OneNetDeviceProvisioningException known) {
            throw known;
        } catch (RestClientResponseException httpFailure) {
            Lookup recovered = safeDetail(deviceName);
            if (recovered != null && recovered.device() != null) {
                return requireMarker(
                        recovered.device(), descriptionMarker, true);
            }
            if (retryableStatus(httpFailure.getStatusCode().value())) {
                throw retryable("ONENET_CREATE_HTTP_RETRYABLE", httpFailure);
            }
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_CREATE_HTTP_REJECTED",
                    "OneNet rejected device creation");
        } catch (RestClientException transportFailure) {
            Lookup recovered = safeDetail(deviceName);
            if (recovered != null && recovered.device() != null) {
                return requireMarker(
                        recovered.device(), descriptionMarker, true);
            }
            throw retryable(
                    "ONENET_CREATE_TRANSPORT_UNAVAILABLE",
                    transportFailure);
        } catch (RuntimeException malformedResponse) {
            throw retryable(
                    "ONENET_CREATE_RESPONSE_INVALID",
                    malformedResponse);
        }
    }

    private Lookup safeDetail(String deviceName) {
        try {
            return detail(deviceName);
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private Lookup detail(String deviceName) {
        URI uri = UriComponentsBuilder.fromUriString(
                        properties.getBaseUrl()
                                + properties.getDeviceDetailPath())
                .queryParam("product_id", properties.getProductId())
                .queryParam("device_name", deviceName)
                .build()
                .encode()
                .toUri();
        try {
            ResponseEntity<String> response = restTemplate.exchange(
                    uri,
                    HttpMethod.GET,
                    new HttpEntity<>(headers()),
                    String.class);
            JsonNode envelope = parse(response.getBody());
            String code = code(envelope);
            if ("0".equals(code)) {
                return new Lookup(device(envelope.path("data")), false, null);
            }
            if (NOT_FOUND_CODES.contains(code)) {
                return new Lookup(null, true, null);
            }
            if (RETRYABLE_CODES.contains(code)) {
                return new Lookup(null, false,
                        OneNetDeviceProvisioningException.retryable(
                                "ONENET_DETAIL_RETRYABLE",
                                "OneNet device detail is temporarily unavailable",
                                null));
            }
            return new Lookup(null, false,
                    OneNetDeviceProvisioningException.permanent(
                            "ONENET_DETAIL_REJECTED",
                            "OneNet rejected device detail query"));
        } catch (RestClientResponseException httpFailure) {
            if (httpFailure.getStatusCode().value() == 404) {
                return new Lookup(null, true, null);
            }
            if (retryableStatus(httpFailure.getStatusCode().value())) {
                throw retryable("ONENET_DETAIL_HTTP_RETRYABLE", httpFailure);
            }
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_DETAIL_HTTP_REJECTED",
                    "OneNet rejected device detail query");
        } catch (RestClientException transportFailure) {
            throw retryable(
                    "ONENET_DETAIL_TRANSPORT_UNAVAILABLE",
                    transportFailure);
        } catch (OneNetDeviceProvisioningException known) {
            throw known;
        } catch (RuntimeException malformedResponse) {
            throw retryable(
                    "ONENET_DETAIL_RESPONSE_INVALID",
                    malformedResponse);
        }
    }

    private OneNetProvisionedDevice requireMarker(
            OneNetProvisionedDevice device,
            String marker,
            boolean requireOwnershipMarker) {
        if (requireOwnershipMarker && !marker.equals(device.description())) {
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_DEVICE_OWNERSHIP_CONFLICT",
                    "OneNet device name is owned by another enrollment");
        }
        return device;
    }

    private OneNetProvisionedDevice device(JsonNode data) {
        String id = text(data, "did");
        String name = text(data, "name");
        String secret = text(data, "sec_key");
        JsonNode descriptionNode = data.get("desc");
        String description = descriptionNode == null
                || descriptionNode.isNull() ? "" : descriptionNode.asString();
        if (id.isBlank() || name.isBlank() || secret.isBlank()) {
            throw new IllegalArgumentException(
                    "OneNet device response is incomplete");
        }
        return new OneNetProvisionedDevice(
                id, name, description, secret);
    }

    private JsonNode parse(String body) {
        if (body == null || body.isBlank()) {
            throw new IllegalArgumentException(
                    "OneNet returned an empty response");
        }
        return objectMapper.readTree(body);
    }

    private HttpHeaders headers() {
        String token = OneNetTokenGenerator.generate(
                properties.getVersion(),
                "products/" + properties.getProductId(),
                properties.getAccessKey(),
                properties.getTokenTtlSeconds());
        HttpHeaders headers = new HttpHeaders();
        headers.setAccept(java.util.List.of(MediaType.APPLICATION_JSON));
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set(HttpHeaders.AUTHORIZATION, token);
        return headers;
    }

    private void requireConfigured() {
        if (!properties.isConfigured()) {
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_NOT_CONFIGURED",
                    "OneNet provisioning is not configured");
        }
    }

    private static String code(JsonNode envelope) {
        JsonNode code = envelope.get("code");
        if (code == null || code.isNull()) {
            throw new IllegalArgumentException(
                    "OneNet response code is missing");
        }
        return code.asString();
    }

    private static String text(JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        return value == null || value.isNull() ? "" : value.asString();
    }

    private static OneNetDeviceProvisioningException retryable(
            String code,
            Throwable cause) {
        return OneNetDeviceProvisioningException.retryable(
                code,
                "OneNet provisioning is temporarily unavailable",
                cause);
    }

    private static boolean retryableStatus(int status) {
        return status == 408 || status == 409 || status == 425
                || status == 429 || status >= 500;
    }

    private record Lookup(
            OneNetProvisionedDevice device,
            boolean missing,
            OneNetDeviceProvisioningException failure) {
    }
}
