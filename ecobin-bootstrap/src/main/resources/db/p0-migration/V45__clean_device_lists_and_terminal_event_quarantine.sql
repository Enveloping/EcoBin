-- Support cleaner device activity filters and terminally quarantine trusted
-- edge events whose immutable business target is no longer authoritative.

ALTER TABLE ops_message_quarantine
    DROP CHECK ck_ops_quarantine_reason,
    ADD CONSTRAINT ck_ops_quarantine_reason CHECK (
        reason_code IN (
            'MISSING_STABLE_ID',
            'PERMANENT_FORMAT_ERROR',
            'UNSUPPORTED_SCHEMA',
            'UNRESOLVED_SCOPE',
            'IDENTITY_CONTENT_CONFLICT',
            'SCOPE_CONFLICT',
            'EVENT_TARGET_NOT_AUTHORITATIVE'
        )
    );

ALTER TABLE rec_delivery_order
    ADD INDEX ix_rec_delivery_asset_received_v45 (
        tenant_id,
        organization_id,
        asset_id,
        backend_received_at DESC,
        id DESC
    );

ALTER TABLE rec_clean_record
    ADD INDEX ix_rec_clean_asset_completed_v45 (
        tenant_id,
        organization_id,
        asset_id,
        completed_at DESC,
        id DESC
    );

ALTER TABLE rec_fullness_event
    ADD INDEX ix_rec_fullness_active_confirmed_v45 (
        tenant_id,
        organization_id,
        status,
        confirmed_at,
        port_id
    );
