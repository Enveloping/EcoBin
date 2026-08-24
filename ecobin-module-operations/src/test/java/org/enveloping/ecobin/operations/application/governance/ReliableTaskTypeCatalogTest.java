package org.enveloping.ecobin.operations.application.governance;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class ReliableTaskTypeCatalogTest {

    @Test
    void knownEntriesHaveUniqueCodesAndHumanReadableExplanations() {
        var entries = ReliableTaskTypeCatalog.entries(List.of());

        assertThat(entries).hasSizeGreaterThanOrEqualTo(20);
        assertThat(entries)
                .allSatisfy(entry -> {
                    assertThat(entry.taskType()).isNotBlank();
                    assertThat(entry.displayName()).isNotBlank();
                    assertThat(entry.description()).isNotBlank();
                    assertThat(entry.executionLanes()).isNotEmpty();
                    assertThat(entry.taskKinds()).isNotEmpty();
                });
        assertThat(entries.stream()
                .map(entry -> entry.taskType())
                .collect(java.util.stream.Collectors.toSet()))
                .hasSameSizeAs(entries);
    }

    @Test
    void observedUnknownTypesRemainVisibleWithAConservativeExplanation() {
        var entries = ReliableTaskTypeCatalog.entries(List.of(
                new ReliableTaskTypeCatalog.Observation(
                        "FUTURE_TASK", "DEVICE", "RECONCILIATION")));

        var unknown = entries.stream()
                .filter(entry -> "FUTURE_TASK".equals(entry.taskType()))
                .findFirst()
                .orElseThrow();
        assertThat(unknown.displayName()).isEqualTo("其他系统任务");
        assertThat(unknown.description()).contains("不要仅凭内部代码执行恢复");
        assertThat(unknown.executionLanes()).containsExactly("DEVICE");
        assertThat(unknown.taskKinds()).containsExactly("RECONCILIATION");
    }

    @Test
    void operationalExplanationsMatchCurrentBusinessFacts() {
        var entries = ReliableTaskTypeCatalog.entries(List.of());

        assertThat(description(entries, "ENSURE_DEVICE_CONFIGURATION"))
                .contains("fixed-frame", "香橙派", "APPLIED")
                .contains("不代表 MCU 已收到或应用配置")
                .doesNotContain("等待边缘保存及 MCU 应用结果");
        assertThat(description(entries, "SAMPLE_FULLNESS"))
                .contains("历史", "V25", "不再创建或下发", "被动上报");
        assertThat(description(entries, "AUTO_REVIEW_DELIVERY_ORDER"))
                .contains("订单创建时", "快照", "不会追溯改变旧订单")
                .doesNotContain("当前规则");
        assertThat(description(entries,
                "QUERY_MERCHANT_TRANSFER_AUTHORIZATION"))
                .contains("手动提现", "自动提现");
    }

    private static String description(
            List<org.enveloping.ecobin.operations.web.v1.OperationsModels
                    .ReliableTaskTypeView> entries,
            String taskType) {
        return entries.stream()
                .filter(entry -> taskType.equals(entry.taskType()))
                .findFirst()
                .orElseThrow()
                .description();
    }
}
