-- The trigger definer is provisioned by the environment before V9.
-- These are the only two trigger families in the P0 epoch.

DELIMITER $$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_iam_miniapp_activation_immutable
BEFORE UPDATE ON iam_organization_miniapp
FOR EACH ROW
BEGIN
    IF OLD.activated_at IS NOT NULL THEN
        IF NOT (NEW.activated_at <=> OLD.activated_at)
            OR NOT (NEW.appid <=> OLD.appid)
            OR NOT (NEW.tenant_id <=> OLD.tenant_id)
            OR NOT (NEW.organization_id <=> OLD.organization_id) THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT =
                    'activated miniapp AppID, scope, and activation time are immutable';
        END IF;
    END IF;
END$$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_iam_org_user_registration_immutable
BEFORE UPDATE ON iam_organization_user
FOR EACH ROW
BEGIN
    IF NOT (NEW.tenant_id <=> OLD.tenant_id)
        OR NOT (NEW.organization_id <=> OLD.organization_id)
        OR NOT (
            NEW.organization_miniapp_id
            <=> OLD.organization_miniapp_id
        )
        OR NOT (NEW.openid <=> OLD.openid)
        OR NOT (NEW.registered_at <=> OLD.registered_at)
        OR NOT (
            NEW.registered_via_deployment_id
            <=> OLD.registered_via_deployment_id
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'organization user registration identity and attribution are immutable';
    END IF;
END$$

DELIMITER ;
