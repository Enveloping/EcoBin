package com.chinamobile.iot.pulsar.example;

import com.alibaba.fastjson2.JSONObject;
import com.chinamobile.iot.pulsar.auth.AESBase64Utils;
import com.chinamobile.iot.pulsar.auth.IoTConsumer;
import com.chinamobile.iot.pulsar.auth.IoTMessage;
import com.chinamobile.iot.pulsar.config.IoTConfig;
import io.netty.util.internal.StringUtil;
import org.apache.pulsar.client.api.MessageId;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

// Vendor demo copy. Supply credentials outside source control before running.
public class IoTPulsarConsume {
    private static final Logger logger =
            LoggerFactory.getLogger(IoTPulsarConsume.class);

    // TODO need to set iotAccessId 消费组ID
    private static String iotAccessId = "";

    // TODO need to set iotSecretKey 消费组KEY
    private static String iotSecretKey = "";

    // TODO 订阅名称
    private static String iotSubscriptionName = "";

    public static void main(String[] args) throws Exception {
        if (StringUtil.isNullOrEmpty(iotAccessId)) {
            logger.error("iotAccessId is null,please input iotAccessId");
            System.exit(1);
        }
        if (StringUtil.isNullOrEmpty(iotSecretKey)) {
            logger.error("iotSecretKey is null,please input iotSecretKey");
            System.exit(1);
        }
        if (StringUtil.isNullOrEmpty(iotSubscriptionName)) {
            logger.error(
                    "iotSubscriptionName is null,please input "
                            + "iotSubscriptionName");
            System.exit(1);
        }

        // TODO 建议收到消息后将消息转到中间件后立即 ACK，避免消息量过大导致消息过期。
        IoTConsumer iotConsumer =
                IoTConsumer.IOTConsumerBuilder.anIOTConsumer()
                        .brokerServerUrl(IoTConfig.brokerSSLServerUrl)
                        .iotAccessId(iotAccessId)
                        .iotSecretKey(iotSecretKey)
                        .subscriptionName(iotSubscriptionName)
                        .iotMessageListener(
                                message -> {
                                    MessageId msgId =
                                            message.getMessageId();
                                    long publishTime =
                                            message.getPublishTime();
                                    String payload =
                                            new String(message.getData());
                                    IoTMessage iotMessage =
                                            JSONObject.parseObject(
                                                    payload,
                                                    IoTMessage.class);
                                    String originalMsg =
                                            AESBase64Utils.decrypt(
                                                    iotMessage.getData(),
                                                    iotSecretKey.substring(
                                                            8,
                                                            24));
                                    logger.info(
                                            "IOT consume message======>>>>>>> "
                                                    + "messageId={}, "
                                                    + "publishTime={}, "
                                                    + "payload={}",
                                            msgId,
                                            publishTime,
                                            payload);
                                    logger.info(
                                            "IOT originalMsg:{}",
                                            originalMsg);
                                })
                        .build();
        iotConsumer.run();
    }
}
