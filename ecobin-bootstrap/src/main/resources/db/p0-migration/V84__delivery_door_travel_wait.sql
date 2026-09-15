-- The actual delivery door reaches its closed travel position in three
-- seconds.  Keep existing immutable configuration releases unchanged; only
-- allow new releases (and the column default) to use the observed value.

ALTER TABLE dev_config_version
    DROP CHECK ck_dev_config_door_travel_wait,
    ALTER COLUMN delivery_door_travel_wait_ms SET DEFAULT 3000,
    ADD CONSTRAINT ck_dev_config_door_travel_wait CHECK (
        delivery_door_travel_wait_ms BETWEEN 3000 AND 45000
    );
