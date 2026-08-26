package org.enveloping.ecobin.recycling.application.bag;

/** Shared issuance limits for one authenticated bag-label batch. */
public final class BagLabelBatchPolicy {

    public static final int MIN_QUANTITY = 1;
    public static final int MAX_QUANTITY = 500;

    private BagLabelBatchPolicy() {
    }

    public static boolean supports(Integer quantity) {
        return quantity != null
                && quantity >= MIN_QUANTITY
                && quantity <= MAX_QUANTITY;
    }
}
