package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class OneNetDeliveryIssueIngressTest {
    @ParameterizedTest
    @CsvSource({"delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,,",
            "delivery-issue-evidence-appended,deliveryIssueEvidenceAppended,DELIVERY_ISSUE_EVIDENCE_APPENDED,,",
            "delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,targetMcuBootId,101",
            "delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,sourceMcuBootId,100",
            "delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,portNo,1",
            "delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,originalStartPayloadHex,00",
            "delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,originalStartPayloadHex,CORRUPT_DIGEST",
            "delivery-issue-archived,deliveryIssueArchived,DELIVERY_ISSUE_ARCHIVED,bootObservationPayloadHex,00",
            "delivery-issue-evidence-appended,deliveryIssueEvidenceAppended,DELIVERY_ISSUE_EVIDENCE_APPENDED,partIndex,2",
            "delivery-issue-evidence-appended,deliveryIssueEvidenceAppended,DELIVERY_ISSUE_EVIDENCE_APPENDED,partCount,2",
            "delivery-issue-evidence-appended,deliveryIssueEvidenceAppended,DELIVERY_ISSUE_EVIDENCE_APPENDED,evidenceSizeBytes,256",
            "delivery-issue-evidence-appended,deliveryIssueEvidenceAppended,DELIVERY_ISSUE_EVIDENCE_APPENDED,evidenceIndex,1",
            "delivery-issue-evidence-appended,deliveryIssueEvidenceAppended,DELIVERY_ISSUE_EVIDENCE_APPENDED,dataHex,00"})
    void receivesOnlyIssueFactsUnderThePermanentDeviceScope(String file, String identifier, String kind,
            String changedField, String changedValue) throws Exception {
        var mapper = JsonMapper.builder().build();
        var inbox = mock(TrustedInboxPort.class);
        var source = mock(TrustedDeviceSourceScopePort.class);
        when(source.resolverForPlatformAsset("SN-CONTRACT-0001")).thenReturn(TrustedInboxScopeResolver.platform());
        when(inbox.receive(any())).thenReturn(new TrustedInboxReceipt(TrustedInboxReceiptState.ACCEPTED,
                UUID.randomUUID(), UUID.randomUUID(), null, "a".repeat(64), true));
        var properties = new OneNetProperties(); properties.setProductId("ecobin-product-contract");
        var dispatcher = new OneNetEventDispatcher(inbox, source, properties, new CosProperties(), mapper);
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        var sample = mapper.readTree(Files.readString(root.resolve("contracts/examples/onenet-wire/" + file + ".event-wire.json")));
        var expected = mapper.readTree(Files.readString(root.resolve("contracts/examples/onenet/" + file + ".event.json")));
        ObjectNode wire = (ObjectNode)sample.path("oneJsonPayload").path("params").path(identifier).path("value");
        if (changedField != null) {
            ObjectNode payload = (ObjectNode)expected.path("payload");
            if (changedField.endsWith("Hex")) {
                if ("CORRUPT_DIGEST".equals(changedValue)) {
                    String raw=wire.path(changedField).asText();
                    changedValue=raw.substring(0,raw.length()-1)+(raw.endsWith("0")?"1":"0");
                }
                wire.put(changedField,changedValue); payload.put(changedField,changedValue);
            } else {
                wire.put(changedField,Long.parseLong(changedValue)); payload.put(changedField,Long.parseLong(changedValue));
            }
            @SuppressWarnings("unchecked") Map<String,Object> values = mapper.convertValue(payload,Map.class);
            wire.put("payloadSha256",OneNetCanonicalJson.payloadSha256(values));
        }
        ObjectNode message = mapper.createObjectNode().put("msgType", "thingEvent");
        var sub = message.putObject("subData").put("productId", "ecobin-product-contract").put("deviceName", "SN-CONTRACT-0001");
        sub.putObject("params").putObject(identifier).set("value", sample.path("oneJsonPayload").path("params").path(identifier).path("value"));
        if (changedField != null) {
            assertThrows(OneNetPermanentMessageException.class,
                    () -> dispatcher.handle(mapper.writeValueAsString(message), "issue-test", new byte[]{1}));
            verify(inbox,never()).receive(any());
            return;
        }
        dispatcher.handle(mapper.writeValueAsString(message), "issue-test", new byte[]{1});
        var captor = ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inbox).receive(captor.capture());
        assertEquals(kind, captor.getValue().messageKind());
        assertEquals("onenet.delivery-issue", captor.getValue().sourceNamespace());
        assertEquals(expected, mapper.readTree(captor.getValue().normalizedPayload()).path("event"));
        verify(source).resolverForPlatformAsset("SN-CONTRACT-0001");
        verify(source, never()).resolverForOrganizationAsset(any());
    }
}
