-- Support the Web cleaning-operation list's stable organization-scoped sort
-- and its most common state filter without changing any business facts.

ALTER TABLE rec_clean_operation
    ADD INDEX ix_rec_clean_operation_created (
        tenant_id,
        organization_id,
        created_at DESC,
        id DESC
    ),
    ADD INDEX ix_rec_clean_operation_status_created (
        tenant_id,
        organization_id,
        status,
        created_at DESC,
        id DESC
    );
