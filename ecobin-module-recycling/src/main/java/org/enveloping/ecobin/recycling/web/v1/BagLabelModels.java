package org.enveloping.ecobin.recycling.web.v1;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import org.enveloping.ecobin.recycling.application.bag.BagLabelBatchPolicy;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class BagLabelModels {

    private BagLabelModels() {
    }

    public record CreateBagLabelBatchRequest(
            @NotNull
            @Min(BagLabelBatchPolicy.MIN_QUANTITY)
            @Max(BagLabelBatchPolicy.MAX_QUANTITY)
            Integer quantity) {
    }

    public record PlatformAdminSummary(
            UUID platformAdminUid,
            String displayName) {
    }

    public record BagLabelBatchSummary(
            UUID batchUid,
            String keyId,
            int quantity,
            PlatformAdminSummary createdBy,
            Instant createdAt) {
    }

    public record BagLabelItemView(
            int sequenceNo,
            String bagCode,
            String qrPayload) {
    }

    public record BagLabelBatchView(
            UUID batchUid,
            String keyId,
            int quantity,
            PlatformAdminSummary createdBy,
            Instant createdAt,
            List<BagLabelItemView> labels) {

        public BagLabelBatchView {
            labels = List.copyOf(labels);
        }
    }

    public record PageData<T>(
            List<T> items,
            int page,
            int pageSize,
            long total) {

        public PageData {
            items = List.copyOf(items);
        }
    }
}
