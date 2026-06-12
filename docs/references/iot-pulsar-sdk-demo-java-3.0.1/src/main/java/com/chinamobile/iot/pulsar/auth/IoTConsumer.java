package com.chinamobile.iot.pulsar.auth;

import org.apache.pulsar.client.api.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;



public class IoTConsumer {
    Logger log= LoggerFactory.getLogger(IoTConsumer.class);
    private String brokerServerUrl;

    private String iotAccessId;
    private String iotSecretKey;
    private String subscriptionName;

    private IOTMessageListener iotMessageListener;

    public void run() throws Exception {
        if (brokerServerUrl == null || brokerServerUrl.trim().isEmpty()) {
            throw new IllegalStateException("brokerServerUrl must be initialized");
        }
        if (iotAccessId == null || iotAccessId.trim().isEmpty()) {
            throw new IllegalStateException("iotAccessId must be initialized");
        }
        if (iotSecretKey == null || iotSecretKey.trim().isEmpty()) {
            throw new IllegalStateException("iotSecretKey must be initialized");
        }
        if (iotMessageListener == null) {
            throw new IllegalStateException("iotMessageListener must be initialized");
        }
        PulsarClient client = PulsarClient.builder().serviceUrl(brokerServerUrl).allowTlsInsecureConnection(true)
                .authentication(new IoTAuthentication(iotAccessId, iotSecretKey)).build();
        Consumer<String> consumer = client.newConsumer(Schema.STRING).topic(String.format("%s/iot/event", iotAccessId))
                .subscriptionName(subscriptionName).subscriptionType(SubscriptionType.Failover)
                .autoUpdatePartitions(Boolean.FALSE).subscribe();

        do {
            Message<String> message=null;
            try {
                message = consumer.receive();
                iotMessageListener.handle(message);
            } catch (Throwable t) {
                log.error("error:{}", t.toString());
            }finally {
                consumer.acknowledge(message);
            }
        } while (true);
    }



    public interface IOTMessageListener {

        void handle(Message<String> message) throws Exception;
    }

    public static final class IOTConsumerBuilder {
        private String brokerServerUrl;
        private String iotAccessId;
        private String iotSecretKey;
        private String subscriptionName;
        private IOTMessageListener iotMessageListener;

        private IOTConsumerBuilder() {
        }

        public static IOTConsumerBuilder anIOTConsumer() {
            return new IOTConsumerBuilder();
        }

        public IOTConsumerBuilder brokerServerUrl(String brokerServerUrl) {
            this.brokerServerUrl = brokerServerUrl;
            return this;
        }

        public IOTConsumerBuilder iotAccessId(String iotAccessId) {
            this.iotAccessId = iotAccessId;
            return this;
        }

        public IOTConsumerBuilder iotSecretKey(String iotSecretKey) {
            this.iotSecretKey = iotSecretKey;
            return this;
        }

        public IOTConsumerBuilder subscriptionName(String subscriptionName) {
            this.subscriptionName = subscriptionName;
            return this;
        }

        public IOTConsumerBuilder iotMessageListener(IOTMessageListener iotMessageListener) {
            this.iotMessageListener = iotMessageListener;
            return this;
        }

        public IoTConsumer build() {
            IoTConsumer iOTConsumer = new IoTConsumer();
            iOTConsumer.iotMessageListener = this.iotMessageListener;
            iOTConsumer.subscriptionName = this.subscriptionName;
            iOTConsumer.iotSecretKey = this.iotSecretKey;
            iOTConsumer.brokerServerUrl = this.brokerServerUrl;
            iOTConsumer.iotAccessId = this.iotAccessId;
            return iOTConsumer;
        }
    }
}
