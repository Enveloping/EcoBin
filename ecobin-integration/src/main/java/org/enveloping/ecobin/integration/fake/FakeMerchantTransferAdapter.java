package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Fake 商家转账：先等待用户确认，查询后按配置收敛成功。 */
final class FakeMerchantTransferAdapter implements MerchantTransferChannelPort {

    private final Map<String, Integer> queries = new ConcurrentHashMap<>();
    private final Map<String, MerchantTransferRequest> requests =
            new ConcurrentHashMap<>();
    private final boolean autoSucceed;
    private final Map<String, TransferEvidence> authorizedRequests =
            new ConcurrentHashMap<>();

    FakeMerchantTransferAdapter(boolean autoSucceed) {
        this.autoSucceed = autoSucceed;
    }

    @Override
    public MerchantTransferResult submitAuthorized(
            AuthorizedMerchantTransferRequest request) {
        queries.putIfAbsent(request.outBillNo(), 0);
        authorizedRequests.put(request.outBillNo(), new TransferEvidence(
                request.mchid(), request.appid(), request.outBillNo(),
                request.amountCent(), request.expectedOpenid()));
        return new MerchantTransferResult(
                MerchantTransferResult.Outcome.PROCESSING,
                "PROCESSING", "FAKEBILL" + request.outBillNo(),
                null, null, null, "SIMULATED", Instant.now(),
                request.mchid(), request.outBillNo(), request.appid(),
                request.amountCent(), request.expectedOpenid());
    }

    @Override
    public MerchantTransferResult submit(MerchantTransferRequest request) {
        queries.putIfAbsent(request.outBillNo(), 0);
        requests.put(request.outBillNo(), request);
        return new MerchantTransferResult(
                MerchantTransferResult.Outcome.WAIT_USER_CONFIRM,
                "WAIT_USER_CONFIRM",
                "FAKEBILL" + request.outBillNo(),
                "fake-package-" + request.outBillNo(),
                null, null, "SIMULATED", Instant.now(),
                null, request.outBillNo(), null, null, null);
    }

    @Override
    public MerchantTransferResult query(MerchantTransferQuery query) {
        MerchantTransferRequest request = requests.get(query.outBillNo());
        TransferEvidence authorized = authorizedRequests.get(
                query.outBillNo());
        if (request == null && authorized == null) {
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.NOT_FOUND,
                    "NOT_FOUND", null, null, "NOT_FOUND", null,
                    "SIMULATED", Instant.now());
        }
        int count = queries.merge(query.outBillNo(), 1, Integer::sum);
        if (autoSucceed && count >= 1) {
            String mchid = request == null
                    ? authorized.mchid() : request.mchid();
            String appid = request == null
                    ? authorized.appid() : request.appid();
            long amount = request == null
                    ? authorized.amountCent() : request.amountCent();
            String openid = request == null
                    ? authorized.openid() : request.openid();
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.SUCCESS,
                    "SUCCESS", "FAKEBILL" + query.outBillNo(),
                    null, null, null, "SIMULATED", Instant.now(),
                    mchid, query.outBillNo(), appid, amount, openid);
        }
        if (request == null) {
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.PROCESSING,
                    "PROCESSING", "FAKEBILL" + query.outBillNo(),
                    null, null, null, "SIMULATED", Instant.now(),
                    authorized.mchid(), authorized.outBillNo(),
                    authorized.appid(), authorized.amountCent(),
                    authorized.openid());
        }
        return new MerchantTransferResult(
                MerchantTransferResult.Outcome.WAIT_USER_CONFIRM,
                "WAIT_USER_CONFIRM", "FAKEBILL" + query.outBillNo(),
                "fake-package-" + query.outBillNo(),
                null, null, "SIMULATED", Instant.now(),
                request.mchid(), request.outBillNo(), request.appid(),
                request.amountCent(), request.openid());
    }

    private record TransferEvidence(
            String mchid,
            String appid,
            String outBillNo,
            long amountCent,
            String openid) {
    }

}
