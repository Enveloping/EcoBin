-- 清运改造：开门即建单。订单上记录本次换上的新空袋编号（open 时小程序扫到，待 cleanTare 补去皮重）。
-- bag_qr 仍表示本次清走的旧袋；new_bag_qr 为新换上的空袋。
ALTER TABLE biz_clean_order
    ADD COLUMN new_bag_qr VARCHAR(64) DEFAULT NULL COMMENT '本次换上的新空袋编号(待去皮)';
