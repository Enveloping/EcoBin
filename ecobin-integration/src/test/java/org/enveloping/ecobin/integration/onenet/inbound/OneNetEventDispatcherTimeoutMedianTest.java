package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Tests the external wire boundary; inbox persistence is outside this adapter. */
class OneNetEventDispatcherTimeoutMedianTest {
    private static final String PRODUCT = "ecobin-product-contract";
    private static final String DEVICE = "SN-CONTRACT-0001";
    private static final byte[] TRANSPORT =
            "offline-median-transport".getBytes(StandardCharsets.UTF_8);

    private final JsonMapper mapper = JsonMapper.builder().build();
    private TrustedInboxPort inbox;
    private OneNetEventDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        inbox = mock(TrustedInboxPort.class);
        var source = mock(TrustedDeviceSourceScopePort.class);
        TrustedInboxScopeResolver resolver = writer -> writer.organization(11, 22);
        when(source.resolverForOrganizationAsset(DEVICE)).thenReturn(resolver);
        when(source.resolverForPermanentAssetFact(any(), any())).thenReturn(resolver);
        when(inbox.receive(any())).thenReturn(new TrustedInboxReceipt(
                TrustedInboxReceiptState.ACCEPTED, UUID.randomUUID(),
                UUID.randomUUID(), null, "a".repeat(64), true));
        var properties = new OneNetProperties();
        properties.setProductId(PRODUCT);
        var cos = new CosProperties();
        cos.setBaseUrl("https://ecobin-contract-1250000000.cos.ap-guangzhou.myqcloud.com");
        dispatcher = new OneNetEventDispatcher(inbox, source, properties, cos, mapper);
    }

    @ParameterizedTest
    @ValueSource(strings = {"firstPreOpenMeasurement", "finalPostCloseMeasurement"})
    void deliveryPreservesUsableMedianWithoutRelabelingOrAddingReview(String slot)
            throws Exception {
        Fixture fixture = fixture("delivery-complete", "deliveryComplete");
        makeMedian((ObjectNode) fixture.wire().path(slot),
                (ObjectNode) fixture.payload().path(slot));

        JsonNode payload = accept(fixture);

        assertEquals(fixture.payload(), payload);
        assertEquals("UNSTABLE", payload.path(slot).path("status").asText());
        assertEquals("TIMEOUT_MEDIAN", payload.path(slot).path("weightValueKind").asText());
        assertEquals(false, payload.path("manualReviewRequired").asBoolean());
    }

    @Test
    void edgeOwnedFullnessPreservesMedianAndDoesNotInventACommand() throws Exception {
        Fixture fixture = fixture("fullness-state-changed", "fullnessStateChanged");
        makeMedian((ObjectNode) fixture.wire().path("totalWeightMeasurement"),
                (ObjectNode) fixture.payload().path("totalWeightMeasurement"));
        fixture.wire().put("confirmationBasis", 2);
        fixture.payload().put("confirmationBasis", "MCU_INDEPENDENT_RECHECK");
        assertEquals(fixture.payload(), accept(fixture));
    }

    @ParameterizedTest
    @CsvSource({"delivery-complete,deliveryComplete,firstPreOpenMeasurement",
            "delivery-complete,deliveryComplete,finalPostCloseMeasurement",
            "clean-complete,cleanComplete,preUnlockMeasurement",
            "clean-complete,cleanComplete,cleanerConfirmedFinalMeasurement"})
    void nativeMeasurementIdentityIsPreservedExactly(String file, String kind, String slot) throws Exception {
        Fixture fixture = fixture(file, kind);
        ObjectNode semantic = (ObjectNode) fixture.payload().path(slot);
        String hex = "45424d31" + String.format("%016x%08x",
                semantic.path("mcuBootId").asLong(), semantic.path("mcuEventSequence").asLong());
        String uid = hex.substring(0, 8) + "-" + hex.substring(8, 12) + "-" + hex.substring(12, 16)
                + "-" + hex.substring(16, 20) + "-" + hex.substring(20);
        ((ObjectNode) fixture.wire().path(slot)).put("measurementUid", uid);
        semantic.put("measurementUid", uid);
        assertEquals(fixture.payload(), accept(fixture));
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void fullnessRejectsContradictoryMedianAtTheWireBoundary(
            String field, Object wireValue, Object semanticValue) throws Exception {
        Fixture fixture = fixture("fullness-state-changed", "fullnessStateChanged");
        ObjectNode wire = (ObjectNode) fixture.wire().path("totalWeightMeasurement");
        ObjectNode semantic = (ObjectNode) fixture.payload().path("totalWeightMeasurement");
        makeMedian(wire, semantic);
        wire.set(field, mapper.valueToTree(wireValue));
        semantic.set(field, mapper.valueToTree(semanticValue));
        reject(fixture);
    }

    private static void makeMedian(ObjectNode wire, ObjectNode semantic) {
        wire.put("status", 2).put("weightValueKind", 6)
                .put("sampleCount", 20).put("measurementElapsedMs", 5000)
                .put("faultCodePresent", false);
        semantic.put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("sampleCount", 20).put("measurementElapsedMs", 5000)
                .putNull("faultCode");
    }

    @ParameterizedTest
    @ValueSource(strings = {"preUnlockMeasurement", "cleanerConfirmedFinalMeasurement"})
    void cleanPreservesMedianAndTheConfirmedNewBagBaseline(String slot) throws Exception {
        Fixture fixture = fixture("clean-complete", "cleanComplete");
        makeMedian((ObjectNode) fixture.wire().path(slot),
                (ObjectNode) fixture.payload().path(slot));

        JsonNode payload = accept(fixture);

        assertEquals(fixture.payload(), payload);
        assertEquals(payload.path("cleanerConfirmedFinalMeasurement").path("reportedWeightGrams"),
                payload.path("newBaselineWeightGrams"));
        assertEquals(true, payload.path("cleanerCompletionConfirmed").asBoolean());
    }

    @ParameterizedTest
    @ValueSource(strings = {"preUnlockMeasurement", "cleanerConfirmedFinalMeasurement"})
    void aMeanLabelAloneDoesNotTurnAFailedCleanMeasurementIntoAUsableWeight(String slot)
            throws Exception {
        Fixture fixture = fixture("clean-complete", "cleanComplete");
        ((ObjectNode) fixture.wire().path(slot)).put("status", 3);
        ((ObjectNode) fixture.payload().path(slot)).put("status", "TIMEOUT");
        reject(fixture);
    }

    private void reject(Fixture fixture) {
        fixture.wire().put("payloadSha256", hash(fixture.payload()));
        assertThrows(OneNetPermanentMessageException.class,
                () -> dispatcher.handle(decrypted(fixture), "offline-invalid-median", TRANSPORT));
        verify(inbox, never()).receive(any());
        verify(inbox).quarantine(any());
    }

    @ParameterizedTest
    @ValueSource(ints = {0, 1})
    void runtimePreservesMedianIdentityQualityAndRealZeroForEachPort(int portIndex)
            throws Exception {
        Fixture fixture = fixture("device-runtime-snapshot", "deviceRuntimeSnapshot");
        makeRuntimeMedian((ObjectNode) fixture.wire().path("ports").get(portIndex),
                (ObjectNode) fixture.payload().path("ports").get(portIndex));

        assertEquals(fixture.payload(), accept(fixture));
    }

    private static void makeRuntimeMedian(ObjectNode wire, ObjectNode semantic) {
        wire.put("weightMeasurementStatus", 2).put("weightValueKind", 6)
                .put("weightSampleCount", 5).put("measurementElapsedMs", 5000)
                .put("weightFaultCodePresent", false).put("reportedWeightGrams", 0);
        semantic.put("weightMeasurementStatus", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("weightSampleCount", 5).put("measurementElapsedMs", 5000)
                .putNull("weightFaultCode").put("reportedWeightGrams", 0);
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void deliveryRejectsIncompleteOrContradictoryMedianEvidence(
            String field, Object wireValue, Object semanticValue) throws Exception {
        Fixture fixture = fixture("delivery-complete", "deliveryComplete");
        ObjectNode wire = (ObjectNode) fixture.wire().path("finalPostCloseMeasurement");
        ObjectNode semantic = (ObjectNode) fixture.payload().path("finalPostCloseMeasurement");
        makeMedian(wire, semantic);
        changeField(wire, semantic, field, wireValue, semanticValue);
        reject(fixture);
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void runtimeRejectsIncompleteOrContradictoryMedianEvidence(
            String field, Object wireValue, Object semanticValue) throws Exception {
        Fixture fixture = fixture("device-runtime-snapshot", "deviceRuntimeSnapshot");
        ObjectNode wire = (ObjectNode) fixture.wire().path("ports").get(0);
        ObjectNode semantic = (ObjectNode) fixture.payload().path("ports").get(0);
        makeRuntimeMedian(wire, semantic);
        String runtimeField = switch (field) {
            case "status" -> "weightMeasurementStatus";
            case "sampleCount" -> "weightSampleCount";
            case "sensorHealth" -> "weightSensorHealth";
            case "faultCode" -> "weightFaultCode";
            case "measurementUid" -> "weightMeasurementUid";
            case "mcuBootId" -> "weightMcuBootId";
            case "mcuEventSequence" -> "weightMcuEventSequence";
            default -> field;
        };
        changeField(wire, semantic, runtimeField, wireValue, semanticValue);
        reject(fixture);
    }

    private void changeField(ObjectNode wire, ObjectNode semantic,
                             String field, Object wireValue, Object semanticValue) {
        semantic.set(field, mapper.valueToTree(semanticValue));
        if (wire.has(field + "Present")) {
            wire.put(field + "Present", semanticValue != null);
            if (semanticValue == null) {
                return; // Legal wire placeholder remains; absence must still be rejected.
            }
        }
        wire.set(field, mapper.valueToTree(wireValue));
    }

    private static Stream<Arguments> invalidMedianFields() {
        return Stream.of(
                Arguments.of("sampleCount", 4, 4),
                Arguments.of("sampleCount", 33, 33),
                Arguments.of("sampleCount", true, true),
                Arguments.of("measurementElapsedMs", 4999, 4999),
                Arguments.of("measurementElapsedMs", 5001, 5001),
                Arguments.of("status", 1, "STABLE"),
                Arguments.of("status", 3, "TIMEOUT"),
                Arguments.of("sensorHealth", 5, "UNKNOWN"),
                Arguments.of("faultCode", 6, "WEIGHT_UNSTABLE"),
                Arguments.of("weightValueAvailable", false, false),
                Arguments.of("reportedWeightGrams", null, null),
                Arguments.of("reportedWeightGrams", -2147483649L, -2147483649L),
                Arguments.of("reportedWeightGrams", 2147483648L, 2147483648L),
                Arguments.of("measurementUid", null, null),
                Arguments.of("mcuBootId", null, null),
                Arguments.of("mcuEventSequence", null, null),
                Arguments.of("calibrationVersion", 4294967296L, 4294967296L),
                Arguments.of("mcuEventSequence", 4294967296L, 4294967296L));
    }

    @ParameterizedTest
    @CsvSource({"5,0", "32,-9", "5,-2147483648", "32,2147483647"})
    void medianTransportPreservesSignedValuesAndSampleBoundariesNotCalibrationApproval(
            int sampleCount, long grams) throws Exception {
        Fixture fixture = fixture("delivery-complete", "deliveryComplete");
        ObjectNode wire = (ObjectNode) fixture.wire().path("finalPostCloseMeasurement");
        ObjectNode semantic = (ObjectNode) fixture.payload().path("finalPostCloseMeasurement");
        makeMedian(wire, semantic);
        changeField(wire, semantic, "sampleCount", sampleCount, sampleCount);
        changeField(wire, semantic, "reportedWeightGrams", grams, grams);
        long net = grams - fixture.payload().path("firstPreOpenMeasurement")
                .path("reportedWeightGrams").asLong();
        fixture.wire().put("deliveryNetWeightGrams", net);
        fixture.payload().put("deliveryNetWeightGrams", net);
        JsonNode accepted = accept(fixture);
        assertEquals(grams, accepted.path("finalPostCloseMeasurement").path("reportedWeightGrams").asLong());
        assertEquals(sampleCount, accepted.path("finalPostCloseMeasurement").path("sampleCount").asInt());
        assertEquals(net, accepted.path("deliveryNetWeightGrams").asLong());
    }

    @ParameterizedTest
    @CsvSource({"3,LAST_FOUR_MEAN", "4,AVAILABLE_SAMPLES_MEAN"})
    void legacyUnstableMeansKeepTheirFaultRatherThanBecomingUsableMedians(int code, String kind)
            throws Exception {
        Fixture fixture = fixture("delivery-complete", "deliveryComplete");
        ObjectNode wire = (ObjectNode) fixture.wire().path("finalPostCloseMeasurement");
        ObjectNode semantic = (ObjectNode) fixture.payload().path("finalPostCloseMeasurement");
        makeMedian(wire, semantic);
        changeField(wire, semantic, "weightValueKind", code, kind);
        changeField(wire, semantic, "sampleCount", 4, 4);
        changeField(wire, semantic, "measurementElapsedMs", 1200, 1200);
        changeField(wire, semantic, "faultCode", 6, "WEIGHT_UNSTABLE");
        assertEquals(fixture.payload(), accept(fixture));
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void cleanMedianStillRequiresAnExactNewBaseline(boolean missing) throws Exception {
        Fixture fixture = fixture("clean-complete", "cleanComplete");
        makeMedian((ObjectNode) fixture.wire().path("cleanerConfirmedFinalMeasurement"),
                (ObjectNode) fixture.payload().path("cleanerConfirmedFinalMeasurement"));
        Long grams = missing ? null : fixture.payload().path("newBaselineWeightGrams").asLong() + 1;
        changeField(fixture.wire(), fixture.payload(), "newBaselineWeightGrams", grams, grams);
        reject(fixture);
    }

    @Test
    void fixedFrameRuntimeStillUsesItsOriginalSingleObservedValue() throws Exception {
        Fixture fixture = fixture("device-runtime-snapshot", "deviceRuntimeSnapshot");
        changeField(fixture.wire(), fixture.payload(), "uartProtocolMajor", null, null);
        changeField(fixture.wire(), fixture.payload(), "uartProtocolMinor", null, null);
        for (int index = 0; index < fixture.wire().path("ports").size(); index++) {
            ObjectNode wire = (ObjectNode) fixture.wire().path("ports").get(index);
            ObjectNode semantic = (ObjectNode) fixture.payload().path("ports").get(index);
            changeField(wire, semantic, "weightValueKind", 5, "LAST_OBSERVED");
            changeField(wire, semantic, "weightSampleCount", 1, 1);
        }
        assertEquals(fixture.payload(), accept(fixture));
    }

    private JsonNode accept(Fixture fixture) throws Exception {
        fixture.wire().put("payloadSha256", hash(fixture.payload()));
        dispatcher.handle(decrypted(fixture), "offline-median", TRANSPORT);
        var captor = ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inbox).receive(captor.capture());
        JsonNode event = mapper.readTree(captor.getValue().normalizedPayload()).path("event");
        assertEquals(hash(fixture.payload()), event.path("payloadSha256").asText());
        if ("fullnessStateChanged".equals(fixture.identifier())) {
            assertEquals(true, event.path("commandUid").isNull());
            assertEquals("PORT_FULLNESS_STATE", event.path("target").path("type").asText());
        }
        return event.path("payload");
    }

    private String decrypted(Fixture fixture) {
        ObjectNode root = mapper.createObjectNode().put("msgType", "thingEvent");
        ObjectNode sub = root.putObject("subData");
        sub.put("productId", PRODUCT).put("deviceName", DEVICE);
        sub.putObject("params").putObject(fixture.identifier()).set("value", fixture.wire());
        return mapper.writeValueAsString(root);
    }

    private String hash(JsonNode value) {
        @SuppressWarnings("unchecked")
        Map<String, Object> data = mapper.convertValue(value, Map.class);
        return OneNetCanonicalJson.payloadSha256(data);
    }

    private Fixture fixture(String basename, String identifier) throws Exception {
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) {
            root = root.getParent();
        }
        JsonNode wire = mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet-wire/" + basename + ".event-wire.json")));
        JsonNode event = mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/" + basename + ".event.json")));
        return new Fixture(identifier,
                (ObjectNode) wire.path("oneJsonPayload").path("params")
                        .path(identifier).path("value"),
                (ObjectNode) event.path("payload"));
    }

    private record Fixture(String identifier, ObjectNode wire, ObjectNode payload) {}
}
