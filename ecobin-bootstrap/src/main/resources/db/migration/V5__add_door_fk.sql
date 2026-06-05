ALTER TABLE biz_door
    ADD CONSTRAINT fk_door_device
    FOREIGN KEY (device_id) REFERENCES biz_device(id) ON DELETE CASCADE;
