package org.enveloping.ecobin.integration.wechatpay;

import org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.UUID;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.database.epoch",
        name = "test-bypass",
        havingValue = "false",
        matchIfMissing = true)
public class WechatPayMerchantProfileRegistrar implements ApplicationRunner {

    static final String FAKE_MCHID = "FAKE1900000001";

    private final JdbcTemplate jdbc;
    private final ExternalAdapterModeProperties mode;
    private final WechatPayProperties properties;

    public WechatPayMerchantProfileRegistrar(
            JdbcTemplate jdbc,
            ExternalAdapterModeProperties mode,
            WechatPayProperties properties) {
        this.jdbc = jdbc;
        this.mode = mode;
        this.properties = properties;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        boolean real = mode.getMode()
                == ExternalAdapterModeProperties.Mode.REAL;
        if (real && !properties.isConfigured()) {
            throw new IllegalStateException(
                    "real external mode requires complete WeChat Pay APIv3 configuration");
        }
        String mchid = real ? properties.getMchid() : FAKE_MCHID;
        String sceneId = real ? properties.getTransferSceneId() : "1010";
        LocalDateTime now = jdbc.queryForObject(
                "SELECT CURRENT_TIMESTAMP(3)", LocalDateTime.class);
        jdbc.update("""
                INSERT INTO fund_wechat_merchant_profile (
                    merchant_profile_uid, mchid, merchant_kind, status,
                    scene_id, report_type, report_content,
                    transfer_page_style, non_secret_config_ref,
                    lock_version, created_at, updated_at
                ) SELECT ?, ?, 'ORDINARY_MERCHANT', 'ENABLED', ?,
                          'RECYCLED_GOODS_NAME', 'MIXED_RECYCLABLES',
                          'STANDARD', ?, 0, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM fund_wechat_merchant_profile WHERE mchid = ?
                )
                """, UUID.randomUUID().toString(), mchid, sceneId,
                real ? "configuration:ecobin.funds.wechat-pay"
                        : "fake:local-software-loop",
                now, now, mchid);
        long merchantId = jdbc.queryForObject("""
                SELECT id FROM fund_wechat_merchant_profile WHERE mchid = ?
                """, Long.class, mchid);
        jdbc.update("""
                INSERT IGNORE INTO fund_payout_gate (
                    merchant_profile_id, gate_state, current_pause_event_id,
                    paused_at, lock_version, created_at, updated_at
                ) VALUES (?, 'OPEN', NULL, NULL, 0, ?, ?)
                """, merchantId, now, now);
    }
}
