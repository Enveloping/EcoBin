package org.enveloping.ecobin.operations.application.governance;

import java.util.Set;

final class ReliableTaskResumptionPolicy {

    private static final Set<String> FUNDS_TYPES = Set.of(
            "CREATE_NATIVE_PAYMENT", "QUERY_NATIVE_PAYMENT",
            "CLOSE_NATIVE_PAYMENT", "POST_RECHARGE_NET_AMOUNT",
            "SUBMIT_MERCHANT_TRANSFER", "QUERY_MERCHANT_TRANSFER",
            "CREATE_MERCHANT_TRANSFER_AUTHORIZATION",
            "QUERY_MERCHANT_TRANSFER_AUTHORIZATION",
            "CANCEL_MERCHANT_TRANSFER", "PROCESS_INBOX");
    private static final Set<String> DEVICE_TYPES = Set.of(
            "PROCESS_INBOX", "SAMPLE_FULLNESS",
            "PROVIDE_PHOTO_UPLOAD_GRANT", "CONFIRM_EDGE_EVENT");

    private ReliableTaskResumptionPolicy() { }

    static boolean supported(String lane, String type) {
        return ("DEVICE".equals(lane) && DEVICE_TYPES.contains(type))
                || ("FUNDS".equals(lane) && FUNDS_TYPES.contains(type));
    }
}
