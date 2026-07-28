package org.enveloping.ecobin.integration.onenet.inbound;

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
     * 实现必须保证幂等；暂时性失败应抛出异常，让消费者 negative ACK。
     *
     * @param decryptedJson   解密后的第二层明文 JSON
     * @param mqMessageId     MQ 传输层消息 id，仅用于安全诊断
     * @param rawTransportBody 第一层原始传输字节，供可靠 inbox 保存证据
     */
    void handle(
            String decryptedJson,
            String mqMessageId,
            byte[] rawTransportBody);
}
