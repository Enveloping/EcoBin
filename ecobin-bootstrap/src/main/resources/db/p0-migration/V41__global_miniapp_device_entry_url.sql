-- V41：普通设备二维码入口改为应用级全局配置。
--
-- 渠道只保存微信 AppID/AppSecret 与启停状态；设备公开码决定机构，入口域名不再是
-- 渠道或机构数据。旧列尚未进入正式业务使用，因此直接前向删除，不保留双模型。

ALTER TABLE iam_miniapp_channel
    DROP CHECK ck_iam_channel_entry_url,
    DROP COLUMN entry_base_url;
