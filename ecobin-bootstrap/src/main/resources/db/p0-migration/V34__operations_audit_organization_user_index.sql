-- Platform audit queries resolve an organization-user public UID to its
-- internal id, then filter by that id without a tenant or organization prefix.
-- Keep the audit timeline ordering in the same index so the platform query can
-- seek directly to one actor and scan its newest records first.
ALTER TABLE ops_audit_log
    ADD INDEX ix_ops_audit_org_user_time (
        organization_user_id,
        occurred_at,
        id
    );
