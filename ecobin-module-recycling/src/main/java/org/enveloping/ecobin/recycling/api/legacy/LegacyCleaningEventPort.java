package org.enveloping.ecobin.recycling.api.legacy;

/**
 * OneNet/HTTP 上行适配器调用旧清运行为的公开入口。
 */
public interface LegacyCleaningEventPort {

    void acceptGross(LegacyCleanGrossCommand command);

    void acceptTare(LegacyCleanTareCommand command);
}
