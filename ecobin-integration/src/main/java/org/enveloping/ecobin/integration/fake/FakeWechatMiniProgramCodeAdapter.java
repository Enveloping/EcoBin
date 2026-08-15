package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.identity.api.port.WechatMiniProgramCodePort;

import java.util.Base64;

/** Deterministic, network-free PNG used by local and test profiles. */
final class FakeWechatMiniProgramCodeAdapter
        implements WechatMiniProgramCodePort {

    private static final byte[] ONE_PIXEL_PNG = Base64.getDecoder().decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=");

    @Override
    public byte[] generate(
            String appId,
            String appSecret,
            String page,
            String scene) {
        return ONE_PIXEL_PNG.clone();
    }
}
