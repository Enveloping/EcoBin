-- V59: allow one platform issuance batch to contain up to 500 authenticated
-- bag labels.  The tables remain disposable printing history only.

ALTER TABLE rec_bag_label_batch
    DROP CHECK ck_rec_bag_label_batch_count,
    ADD CONSTRAINT ck_rec_bag_label_batch_count_v59 CHECK (
        label_count BETWEEN 1 AND 500
    );

ALTER TABLE rec_bag_label_item
    DROP CHECK ck_rec_bag_label_item_sequence,
    ADD CONSTRAINT ck_rec_bag_label_item_sequence_v59 CHECK (
        sequence_no BETWEEN 1 AND 500
    );
