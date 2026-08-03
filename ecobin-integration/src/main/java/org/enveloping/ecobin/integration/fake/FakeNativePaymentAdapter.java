package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 只用于 fake 外联模式的 Native 软件状态机。生成的证据带 FAKE 前缀，不能作为真实支付证据。
 */
final class FakeNativePaymentAdapter implements NativePaymentChannelPort {

    private final Map<String, Integer> queryCounts = new ConcurrentHashMap<>();
    private final boolean autoSucceed;

    FakeNativePaymentAdapter(boolean autoSucceed) {
        this.autoSucceed = autoSucceed;
    }

    @Override
    public NativePaymentResult create(NativePaymentRequest request) {
        queryCounts.putIfAbsent(request.outTradeNo(), 0);
        return new NativePaymentResult(
                NativePaymentResult.Outcome.ACCEPTED,
                "SIMULATED_NOTPAY",
                "weixin://wxpay/bizpayurl?pr=FAKE"
                        + request.outTradeNo(),
                null, null, "SIMULATED", Instant.now());
    }

    @Override
    public NativePaymentResult query(NativePaymentQuery query) {
        int count = queryCounts.merge(query.outTradeNo(), 1, Integer::sum);
        if (autoSucceed && count >= 1) {
            return new NativePaymentResult(
                    NativePaymentResult.Outcome.SUCCEEDED,
                    "SIMULATED_SUCCESS",
                    null,
                    "FAKEPAY" + query.outTradeNo(),
                    null, "SIMULATED", Instant.now());
        }
        return new NativePaymentResult(
                NativePaymentResult.Outcome.ACCEPTED,
                "SIMULATED_NOTPAY",
                null, null, null, "SIMULATED", Instant.now());
    }

    @Override
    public NativePaymentResult close(NativePaymentQuery query) {
        return new NativePaymentResult(
                NativePaymentResult.Outcome.CLOSED,
                "SIMULATED_CLOSED",
                null, null, null, "SIMULATED", Instant.now());
    }
}
