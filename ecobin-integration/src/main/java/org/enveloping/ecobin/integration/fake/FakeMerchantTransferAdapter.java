package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Fake 商家转账：先等待用户确认，查询后按配置收敛成功。 */
final class FakeMerchantTransferAdapter implements MerchantTransferChannelPort {

    private final Map<String, Integer> queries = new ConcurrentHashMap<>();
    private final boolean autoSucceed;

    FakeMerchantTransferAdapter(boolean autoSucceed) {
        this.autoSucceed = autoSucceed;
    }

    @Override
    public MerchantTransferResult submit(MerchantTransferRequest request) {
        queries.putIfAbsent(request.outBillNo(), 0);
        return new MerchantTransferResult(
                MerchantTransferResult.Outcome.WAIT_USER_CONFIRM,
                "WAIT_USER_CONFIRM",
                "FAKEBILL" + request.outBillNo(),
                "fake-package-" + request.outBillNo(),
                null, null, "SIMULATED", Instant.now());
    }

    @Override
    public MerchantTransferResult query(MerchantTransferQuery query) {
        int count = queries.merge(query.outBillNo(), 1, Integer::sum);
        if (autoSucceed && count >= 1) {
            return new MerchantTransferResult(
                    MerchantTransferResult.Outcome.SUCCESS,
                    "SUCCESS", "FAKEBILL" + query.outBillNo(),
                    null, null, null, "SIMULATED", Instant.now());
        }
        return new MerchantTransferResult(
                MerchantTransferResult.Outcome.WAIT_USER_CONFIRM,
                "WAIT_USER_CONFIRM", "FAKEBILL" + query.outBillNo(),
                "fake-package-" + query.outBillNo(),
                null, null, "SIMULATED", Instant.now());
    }

    @Override
    public MerchantTransferResult cancel(MerchantTransferQuery query) {
        return new MerchantTransferResult(
                MerchantTransferResult.Outcome.CANCELLED,
                "CANCELLED", "FAKEBILL" + query.outBillNo(),
                null, null, "SIMULATED_CANCELLED",
                "SIMULATED", Instant.now());
    }
}
