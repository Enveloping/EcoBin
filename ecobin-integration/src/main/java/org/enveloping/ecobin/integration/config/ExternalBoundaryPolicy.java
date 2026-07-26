package org.enveloping.ecobin.integration.config;

import static org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties.Mode.FAKE;
import static org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties.Mode.REAL;

/**
 * Fake/真实外联的纯配置判定。
 */
public final class ExternalBoundaryPolicy {

    private ExternalBoundaryPolicy() {
    }

    public static Verification verify(Snapshot snapshot) {
        if (snapshot.mode() == null) {
            throw new ExternalBoundaryException(
                    "ecobin.external.mode must be FAKE or REAL");
        }
        if (snapshot.mode() == FAKE) {
            if (!snapshot.blockFakeInbound()
                    && !snapshot.legacyTestRuntime()) {
                throw new ExternalBoundaryException(
                        "Fake external ingress blocking can only be disabled by legacy tests");
            }
            if (snapshot.oneNetSubscriptionEnabled()
                    || snapshot.oneNetInboundConfigured()
                    || snapshot.oneNetOutboundConfigured()
                    || snapshot.cosConfigured()
                    || snapshot.wechatConfigured()) {
                throw new ExternalBoundaryException(
                        "Fake mode rejects all real OneNet, COS and WeChat credentials");
            }
            return new Verification(FAKE, snapshot.blockFakeInbound());
        }

        if (snapshot.mode() == REAL) {
            if (!snapshot.oneNetSubscriptionEnabled()
                    || !snapshot.oneNetInboundConfigured()
                    || !snapshot.oneNetOutboundConfigured()
                    || !snapshot.cosConfigured()
                    || !snapshot.wechatConfigured()) {
                throw new ExternalBoundaryException(
                        "Real mode requires complete OneNet, COS and WeChat configuration");
            }
            return new Verification(REAL, false);
        }

        throw new ExternalBoundaryException("unsupported external adapter mode");
    }

    public record Snapshot(
            ExternalAdapterModeProperties.Mode mode,
            boolean blockFakeInbound,
            boolean legacyTestRuntime,
            boolean oneNetSubscriptionEnabled,
            boolean oneNetInboundConfigured,
            boolean oneNetOutboundConfigured,
            boolean cosConfigured,
            boolean wechatConfigured) {
    }

    public record Verification(
            ExternalAdapterModeProperties.Mode mode,
            boolean inboundBlocked) {
    }
}
