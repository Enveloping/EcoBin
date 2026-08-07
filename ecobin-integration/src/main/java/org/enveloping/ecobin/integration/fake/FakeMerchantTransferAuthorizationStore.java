package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort.AuthorizationRequest;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

final class FakeMerchantTransferAuthorizationStore {

    private final Map<String, AuthorizationRequest> requests =
            new ConcurrentHashMap<>();
    private final Map<String, Instant> createdAt =
            new ConcurrentHashMap<>();

    void put(AuthorizationRequest request, Instant channelCreatedAt) {
        requests.putIfAbsent(request.outAuthorizationNo(), request);
        createdAt.putIfAbsent(
                request.outAuthorizationNo(), channelCreatedAt);
    }

    AuthorizationRequest request(String outAuthorizationNo) {
        return requests.get(outAuthorizationNo);
    }

    Instant createdAt(String outAuthorizationNo) {
        return createdAt.get(outAuthorizationNo);
    }

    String activate(String outAuthorizationNo) {
        String value = "FAKEAUTH" + outAuthorizationNo;
        return value.substring(0, Math.min(32, value.length()));
    }
}
