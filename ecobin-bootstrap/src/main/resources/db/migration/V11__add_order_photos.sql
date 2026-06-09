-- 投递 / 清运订单抓拍照片字段（COS 直传模式）
-- 设备开门前 + 关门后分别拍箱内/箱外，共 4 张，逐张异步上传后回填 URL
ALTER TABLE biz_delivery_order ADD COLUMN photo_open_outside  VARCHAR(512) DEFAULT NULL COMMENT '开门前箱外照片URL';
ALTER TABLE biz_delivery_order ADD COLUMN photo_open_inside   VARCHAR(512) DEFAULT NULL COMMENT '开门前箱内照片URL';
ALTER TABLE biz_delivery_order ADD COLUMN photo_close_outside VARCHAR(512) DEFAULT NULL COMMENT '关门后箱外照片URL';
ALTER TABLE biz_delivery_order ADD COLUMN photo_close_inside  VARCHAR(512) DEFAULT NULL COMMENT '关门后箱内照片URL';

ALTER TABLE biz_clean_order ADD COLUMN photo_open_outside  VARCHAR(512) DEFAULT NULL COMMENT '开门前箱外照片URL';
ALTER TABLE biz_clean_order ADD COLUMN photo_open_inside   VARCHAR(512) DEFAULT NULL COMMENT '开门前箱内照片URL';
ALTER TABLE biz_clean_order ADD COLUMN photo_close_outside VARCHAR(512) DEFAULT NULL COMMENT '关门后箱外照片URL';
ALTER TABLE biz_clean_order ADD COLUMN photo_close_inside  VARCHAR(512) DEFAULT NULL COMMENT '关门后箱内照片URL';
