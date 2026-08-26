package org.enveloping.ecobin.recycling.application.bag;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class BagLabelBatchPolicyTest {

    @Test
    void acceptsTheInclusiveOneToFiveHundredBoundary() {
        assertThat(BagLabelBatchPolicy.supports(1)).isTrue();
        assertThat(BagLabelBatchPolicy.supports(500)).isTrue();
    }

    @Test
    void rejectsMissingOrOutOfRangeQuantities() {
        assertThat(BagLabelBatchPolicy.supports(null)).isFalse();
        assertThat(BagLabelBatchPolicy.supports(0)).isFalse();
        assertThat(BagLabelBatchPolicy.supports(501)).isFalse();
    }
}
