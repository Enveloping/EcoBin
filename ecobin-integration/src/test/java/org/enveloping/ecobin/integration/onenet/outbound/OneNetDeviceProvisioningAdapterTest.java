package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningException;
import org.enveloping.ecobin.device.api.result.OneNetProvisionedDevice;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetDeviceProvisioningAdapterTest {

    private static final String DEVICE_NAME = "ECM0-FACTORY-0001";
    private static final String MARKER = "ecobin-enrollment:enrollment-uid";

    private RestTemplate restTemplate;
    private OneNetDeviceProvisioningAdapter adapter;

    @BeforeEach
    void setUp() {
        OneNetProperties properties = new OneNetProperties();
        properties.setBaseUrl("https://onenet.invalid");
        properties.setProductId("product-1");
        properties.setAccessKey("c2FtcGxlLWtleQ==");
        restTemplate = mock(RestTemplate.class);
        adapter = new OneNetDeviceProvisioningAdapter(
                properties, restTemplate, new ObjectMapper());
    }

    @Test
    void createsMissingDeviceAndReturnsOfficialCredentialFields() {
        when(restTemplate.exchange(
                any(URI.class),
                eq(HttpMethod.GET),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":10014}"));
        when(restTemplate.postForEntity(
                anyString(), any(HttpEntity.class), eq(String.class)))
                .thenReturn(ResponseEntity.ok("""
                        {"code":0,"data":{
                          "did":"device-id-1",
                          "name":"ECM0-FACTORY-0001",
                          "sec_key":"one-time-device-secret",
                          "desc":"ecobin-enrollment:enrollment-uid"
                        }}
                        """));

        OneNetProvisionedDevice device = adapter.ensureDevice(
                DEVICE_NAME, MARKER, true);

        assertEquals("device-id-1", device.deviceId());
        assertEquals(DEVICE_NAME, device.deviceName());
        assertEquals(MARKER, device.description());
        assertEquals("one-time-device-secret", device.secretKey());
        assertNotNull(device.secretKey());
    }

    @Test
    void refusesToAdoptAnExistingNameOwnedByAnotherEnrollment() {
        when(restTemplate.exchange(
                any(URI.class),
                eq(HttpMethod.GET),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("""
                        {"code":0,"data":{
                          "did":"device-id-2",
                          "name":"ECM0-FACTORY-0001",
                          "sec_key":"existing-secret",
                          "desc":"another-enrollment"
                        }}
                        """));

        OneNetDeviceProvisioningException failure = assertThrows(
                OneNetDeviceProvisioningException.class,
                () -> adapter.ensureDevice(DEVICE_NAME, MARKER, true));

        assertEquals("ONENET_DEVICE_OWNERSHIP_CONFLICT", failure.code());
        assertFalse(failure.retryable());
        verify(restTemplate, never()).postForEntity(
                anyString(), any(HttpEntity.class), eq(String.class));
    }
}
