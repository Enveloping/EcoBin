-- Organization delivery configuration is an independent sensitive capability.
-- Existing V10 reference data is immutable after deployment, so the new
-- permission is introduced through this forward migration.

INSERT INTO iam_permission_definition (
    permission_code,
    scope_kind,
    permission_name,
    description,
    enabled,
    created_at
) VALUES
    (
        'delivery.configuration.manage',
        'TENANT',
        '管理投递规则',
        '读取并发布本租户机构投递和审核规则。',
        1,
        '2026-08-01 00:00:00.000'
    ),
    (
        'delivery.configuration.manage',
        'ORGANIZATION',
        '管理投递规则',
        '读取并发布当前机构投递和审核规则。',
        1,
        '2026-08-01 00:00:00.000'
    );
