package org.enveloping.ecobin.framework.onenet;

/**
 * OneNet 北向消息处理 SPI。
 * <p>
 * {@link OneNetMqConsumer} 负责连接 / 解密（传输层，位于 framework）；具体的报文解析与业务分发
 * 由业务模块实现本接口（依赖方向 framework ← business，故以接口解耦）。
 * 若容器中没有任何实现，消费者仅打印解密后的明文、不分发。
 */
public interface OneNetMessageHandler {

    /**
     * 处理一条已解密的第二层明文 JSON（{@code {"msgType":..,"subData":..}}）。
     * 实现应自行保证幂等并吞掉异常（北向为 at-least-once，抛出会阻塞 ack）。
     *
     * @param decryptedJson 解密后的第二层明文 JSON
     * @param mqMessageId   MQ 传输层消息 id（Pulsar messageId），作幂等兜底键：
     *                      报文自带 OneNet 消息 id 时优先用报文 id，缺失时回退本值（同一消息重投 id 不变）
     */
    void handle(String decryptedJson, String mqMessageId);
}
