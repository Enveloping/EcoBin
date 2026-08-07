package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort;

import java.time.Instant;

final class FakeMerchantTransferAuthorizationAdapter
        implements MerchantTransferAuthorizationChannelPort {

    private final FakeMerchantTransferAuthorizationStore store;
    private final boolean autoSucceed;

    FakeMerchantTransferAuthorizationAdapter(
            FakeMerchantTransferAuthorizationStore store,
            boolean autoSucceed) {
        this.store = store;
        this.autoSucceed = autoSucceed;
    }

    @Override
    public AuthorizationResult create(AuthorizationRequest request) {
        Instant now = Instant.now();
        store.put(request, now);
        return result(
                request, AuthorizationResult.Outcome.WAIT_USER_CONFIRM,
                "WAIT_USER_CONFIRM", null,
                "fake-authorization-package-" + request.outAuthorizationNo(),
                now, now);
    }

    @Override
    public AuthorizationResult query(AuthorizationQuery query) {
        AuthorizationRequest request = store.request(
                query.outAuthorizationNo());
        if (request == null && query.knownChannelCreatedAt() == null) {
            return new AuthorizationResult(
                    AuthorizationResult.Outcome.NOT_FOUND,
                    null, query.outAuthorizationNo(), null,
                    null, null, null, null, null, null, null,
                    null, null, null, "NOT_FOUND", "SIMULATED",
                    Instant.now());
        }
        Instant now = Instant.now();
        if (autoSucceed) {
            return request == null
                    ? result(
                            query, AuthorizationResult.Outcome.ACTIVE,
                            "TAKING_EFFECT",
                            store.activate(query.outAuthorizationNo()),
                            null, query.knownChannelCreatedAt(), now)
                    : result(
                            request, AuthorizationResult.Outcome.ACTIVE,
                            "TAKING_EFFECT",
                            store.activate(request.outAuthorizationNo()),
                            null,
                            store.createdAt(request.outAuthorizationNo()),
                            now);
        }
        return request == null
                ? result(
                        query,
                        AuthorizationResult.Outcome.WAIT_USER_CONFIRM,
                        "WAIT_USER_CONFIRM", null,
                        "fake-authorization-package-"
                                + query.outAuthorizationNo(),
                        query.knownChannelCreatedAt(), now)
                : result(
                        request,
                        AuthorizationResult.Outcome.WAIT_USER_CONFIRM,
                        "WAIT_USER_CONFIRM", null,
                        "fake-authorization-package-"
                                + request.outAuthorizationNo(),
                        store.createdAt(request.outAuthorizationNo()), now);
    }

    private static AuthorizationResult result(
            AuthorizationRequest request,
            AuthorizationResult.Outcome outcome,
            String state,
            String authorizationId,
            String packageInfo,
            Instant channelCreatedAt,
            Instant observedAt) {
        return new AuthorizationResult(
                outcome, state, request.outAuthorizationNo(),
                authorizationId, request.appid(), request.openid(),
                request.sceneId(), request.userDisplayName(),
                request.userRecvPerception(), packageInfo, null,
                channelCreatedAt,
                outcome == AuthorizationResult.Outcome.ACTIVE
                        ? observedAt : null,
                null,
                null, "SIMULATED", observedAt);
    }

    private static AuthorizationResult result(
            AuthorizationQuery query,
            AuthorizationResult.Outcome outcome,
            String state,
            String authorizationId,
            String packageInfo,
            Instant channelCreatedAt,
            Instant observedAt) {
        return new AuthorizationResult(
                outcome, state, query.outAuthorizationNo(),
                authorizationId, query.appid(), query.openid(),
                query.sceneId(), query.userDisplayName(),
                query.userRecvPerception(), packageInfo, null,
                channelCreatedAt,
                outcome == AuthorizationResult.Outcome.ACTIVE
                        ? observedAt : null,
                null,
                null, "SIMULATED", observedAt);
    }
}
