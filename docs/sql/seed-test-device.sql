-- OneNet 上行联调用测试数据：设备 test-divice-1 + 一个投口（默认机构 tenant_id=1）
-- 幂等，可重复执行。库名按 application.yml 为 ecobin。
-- 用途见 docs/onenet-device-simulation.md 阶段2。

-- 1) 测试设备（sn 唯一键，重复执行只更新名称/状态）
INSERT INTO biz_device (tenant_id, sn, name, type, status)
VALUES (1, 'test-divice-1', 'OneNet联调测试设备', 1, 1)
ON DUPLICATE KEY UPDATE name = VALUES(name), status = VALUES(status);

-- 2) 该设备 1 号投口（清运 open 需要投口；可回收/不区分/启用）
--    biz_door 无 (device_id,door_index) 唯一键，用派生表规避 MySQL 1093 实现"不存在才插"
INSERT INTO biz_door (tenant_id, device_id, door_index, name, waste_type1, waste_type2, enabled, sort_order)
SELECT 1, d.id, 1, '默认投口', 2, 0, 1, 0
FROM biz_device d
WHERE d.sn = 'test-divice-1'
  AND NOT EXISTS (
      SELECT 1 FROM (SELECT device_id, door_index FROM biz_door) bd
      WHERE bd.device_id = d.id AND bd.door_index = 1
  );

-- 查看结果
SELECT d.id AS device_id, d.sn, d.tenant_id, bd.id AS door_id, bd.door_index, bd.waste_type1
FROM biz_device d LEFT JOIN biz_door bd ON bd.device_id = d.id
WHERE d.sn = 'test-divice-1';
