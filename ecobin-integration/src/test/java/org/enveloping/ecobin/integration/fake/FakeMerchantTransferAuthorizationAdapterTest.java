package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class FakeMerchantTransferAuthorizationAdapterTest {

    @Test
    void queryKeepsTheOriginalChannelCreationTime() {
        var request = new MerchantTransferAuthorizationChannelPort
                .AuthorizationRequest(
                "190001", "AU12345678", "wx-app-1", "openid-1",
                "1001", "金收宝用户-12345678", null,
                "https://fake.invalid/authorization-notify");
        var adapter = new FakeMerchantTransferAuthorizationAdapter(
                new FakeMerchantTransferAuthorizationStore(), true);

        var created = adapter.create(request);
        var queried = adapter.query(
                new MerchantTransferAuthorizationChannelPort
                        .AuthorizationQuery(
                        request.mchid(), request.outAuthorizationNo(),
                        request.appid(), request.openid(), request.sceneId(),
                        request.userDisplayName(), request.userRecvPerception(),
                        created.channelCreatedAt()));

        assertEquals(created.channelCreatedAt(), queried.channelCreatedAt());
    }

    @Test
    void persistedRequestEvidenceAllowsQueryAfterAdapterRestart() {
        var request = new MerchantTransferAuthorizationChannelPort
                .AuthorizationRequest(
                "190001", "AU12345678", "wx-app-1", "openid-1",
                "1001", "金收宝用户-12345678", null,
                "https://fake.invalid/authorization-notify");
        var firstAdapter = new FakeMerchantTransferAuthorizationAdapter(
                new FakeMerchantTransferAuthorizationStore(), true);
        var created = firstAdapter.create(request);

        var restartedAdapter = new FakeMerchantTransferAuthorizationAdapter(
                new FakeMerchantTransferAuthorizationStore(), true);
        var result = restartedAdapter.query(
                new MerchantTransferAuthorizationChannelPort
                        .AuthorizationQuery(
                        request.mchid(), request.outAuthorizationNo(),
                        request.appid(), request.openid(), request.sceneId(),
                        request.userDisplayName(), request.userRecvPerception(),
                        created.channelCreatedAt()));

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.ACTIVE,
                result.outcome());
        assertEquals(request.outAuthorizationNo(),
                result.outAuthorizationNo());
        assertEquals(request.appid(), result.appid());
        assertEquals(request.openid(), result.openid());
        assertEquals(request.sceneId(), result.sceneId());
        assertEquals(created.channelCreatedAt(), result.channelCreatedAt());
        assertNotNull(result.authorizedAt());
    }

    @Test
    void missingAuthorizationWithoutPersistedEvidenceRemainsNotFound() {
        var adapter = new FakeMerchantTransferAuthorizationAdapter(
                new FakeMerchantTransferAuthorizationStore(), true);

        var result = adapter.query(
                new MerchantTransferAuthorizationChannelPort
                        .AuthorizationQuery(
                        "190001", "AU12345678", "wx-app-1", "openid-1",
                        "1001", "金收宝用户-12345678", null, null));

        assertEquals(
                MerchantTransferAuthorizationChannelPort.AuthorizationResult
                        .Outcome.NOT_FOUND,
                result.outcome());
    }
}
