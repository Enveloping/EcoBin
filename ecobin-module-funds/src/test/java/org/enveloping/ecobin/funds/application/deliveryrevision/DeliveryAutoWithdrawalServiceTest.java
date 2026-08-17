package org.enveloping.ecobin.funds.application.deliveryrevision;

import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService.ActiveAuthorizationSnapshot;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DeliveryAutoWithdrawalServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 22L;
    private static final long USER_ID = 33L;
    private static final long WALLET_ID = 44L;
    private static final long REVISION_ID = 55L;
    private static final long ACCOUNT_ID = 66L;
    private static final LocalDateTime NOW =
            LocalDateTime.parse("2026-08-17T08:09:10.123");

    private RecordingJdbcTemplate jdbc;
    private ReliableFundsTaskRegistrationPort tasks;
    private AuditPort audit;
    private FundsOperationalControlPort operationalControl;
    private FundsIdentityAccessPort identityAccess;
    private MerchantTransferAuthorizationApplicationService authorization;
    private DeliveryAutoWithdrawalService service;

    @BeforeEach
    void setUp() {
        jdbc = new RecordingJdbcTemplate();
        tasks = mock(ReliableFundsTaskRegistrationPort.class);
        audit = mock(AuditPort.class);
        operationalControl = mock(FundsOperationalControlPort.class);
        identityAccess = mock(FundsIdentityAccessPort.class);
        authorization = mock(
                MerchantTransferAuthorizationApplicationService.class);
        when(audit.append(any())).thenReturn(700L);
        service = new DeliveryAutoWithdrawalService(
                jdbc,
                authorization,
                tasks,
                audit,
                operationalControl,
                identityAccess);
    }

    @Test
    void skipsWhenTheSharedWithdrawalIdentityBoundaryIsNoLongerValid() {
        when(identityAccess.lockWithdrawalTransferIdentity(any()))
                .thenReturn(false);

        DeliveryAutoWithdrawalService.Plan plan = service.prepare(
                command(80L, DeliveryRevisionKind.INITIAL_REVIEW),
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                REVISION_ID);

        assertThat(plan.enabled()).isTrue();
        assertThat(plan.skipReason()).isEqualTo("USER_UNAVAILABLE");
        verify(identityAccess).lockWithdrawalTransferIdentity(any());
        verify(authorization, never()).lockCurrentActive(
                anyLong(), anyLong(), anyLong(), anyLong(), anyLong(),
                anyLong(), anyLong(), any(), any(), any(), any());
    }

    @Test
    void rejectsEnabledConfigurationWithMissingAutomaticAmounts() {
        IllegalStateException failure = assertThrows(
                IllegalStateException.class,
                () -> new DeliveryAutoWithdrawalService.AutoConfig(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        100L,
                        4L,
                        20_000L,
                        true,
                        null,
                        200L,
                        80L));

        assertThat(failure.getMessage())
                .contains("automatic withdrawal configuration");
    }

    @Test
    void createsAndFreezesReviewFreeAutomaticWithdrawalAtomically() {
        service.complete(
                readyPlan(80L),
                command(80L, DeliveryRevisionKind.INITIAL_REVIEW),
                UUID.fromString("10000000-0000-4000-8000-000000000001"),
                wallet(120L),
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(8L, 3L),
                false,
                NOW);

        assertThat(jdbc.sql()).anySatisfy(sql -> assertThat(sql)
                .contains("INSERT INTO fund_withdrawal_order")
                .contains("'DELIVERY_AUTO'")
                .contains("'AUTHORIZED'"));
        assertThat(jdbc.sql()).anySatisfy(sql -> assertThat(sql)
                .contains("INSERT INTO fund_active_withdrawal"));
        assertThat(jdbc.sql()).anySatisfy(sql -> assertThat(sql)
                .contains("UPDATE fund_user_wallet")
                .contains("frozen_withdrawal_cent"));
        assertThat(jdbc.sql()).anySatisfy(sql -> assertThat(sql)
                .contains("UPDATE fund_organization_payout_account"));
        assertThat(jdbc.sql()).anySatisfy(sql -> assertThat(sql)
                .contains("INSERT INTO fund_withdrawal_review")
                .contains("'SYSTEM'"));
        assertThat(jdbc.argumentsFor(
                "fund_delivery_auto_withdrawal_decision"))
                .contains("CREATED_READY_TO_SUBMIT");
        verify(operationalControl)
                .resolveAutoWithdrawalOrganizationLiquidityShortageIfCovered(
                        TENANT_ID, ORGANIZATION_ID, 1_000L, NOW);

        ArgumentCaptor<ReliableFundsTaskRegistrationPort
                .ReliableFundsTaskRegistration> task =
                ArgumentCaptor.forClass(ReliableFundsTaskRegistrationPort
                        .ReliableFundsTaskRegistration.class);
        verify(tasks).register(task.capture());
        assertThat(task.getValue().taskType())
                .isEqualTo("SUBMIT_MERCHANT_TRANSFER");
        assertThat(task.getValue().targetStableKey())
                .startsWith("AW");
    }

    @Test
    void createsReviewRequiredWithdrawalWithoutSubmittingWechatTask() {
        service.complete(
                readyPlan(50L),
                command(80L, DeliveryRevisionKind.INITIAL_REVIEW),
                UUID.fromString("10000000-0000-4000-8000-000000000001"),
                wallet(120L),
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(8L, 3L),
                false,
                NOW);

        assertThat(jdbc.argumentsFor("INSERT INTO fund_withdrawal_order"))
                .contains("PENDING_REVIEW");
        assertThat(jdbc.sql()).allSatisfy(sql -> assertThat(sql)
                .doesNotContain("INSERT INTO fund_withdrawal_review"));
        verify(tasks, never()).register(any());
        verify(audit, never()).append(any());
    }

    @Test
    void recordsBusinessSkipWithoutCreatingOrFreezingWithdrawal() {
        service.complete(
                readyPlan(80L),
                command(80L, DeliveryRevisionKind.INITIAL_REVIEW),
                UUID.fromString("10000000-0000-4000-8000-000000000001"),
                wallet(-1L),
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(8L, 3L),
                false,
                NOW);

        assertThat(jdbc.sql()).hasSize(1);
        assertThat(jdbc.sql().getFirst())
                .contains("fund_delivery_auto_withdrawal_decision");
        assertThat(jdbc.argumentsFor(
                "fund_delivery_auto_withdrawal_decision"))
                .contains("SKIPPED", "PREVIOUS_WALLET_BALANCE_NEGATIVE");
        verify(tasks, never()).register(any());
    }

    private static DeliveryAutoWithdrawalService.Plan readyPlan(
            long reviewFreeCent) {
        var config = new DeliveryAutoWithdrawalService.AutoConfig(
                TENANT_ID,
                ORGANIZATION_ID,
                100L,
                4L,
                20_000L,
                true,
                10L,
                200L,
                reviewFreeCent);
        var user = new DeliveryAutoWithdrawalService.UserIdentity(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                200L,
                300L,
                "+8613800000000",
                "ACTIVE",
                "openid-test",
                "appid-test");
        var binding = new DeliveryAutoWithdrawalService.Binding(
                400L,
                200L,
                500L,
                "appid-test",
                "mchid-test",
                "scene-test");
        var authorization = new ActiveAuthorizationSnapshot(
                600L,
                "OA00000001",
                "AUTH00000001",
                500L,
                400L,
                200L,
                "mchid-test",
                "appid-test",
                "openid-test",
                "scene-test");
        return DeliveryAutoWithdrawalService.Plan.ready(
                config,
                REVISION_ID,
                user,
                binding,
                authorization);
    }

    private static DeliveryRevisionDeltaRepository.WalletRow wallet(
            long availableCent) {
        return new DeliveryRevisionDeltaRepository.WalletRow(
                WALLET_ID,
                availableCent,
                5L,
                7L,
                "OPEN",
                null,
                null,
                null,
                2L);
    }

    private static ApplyDeliveryRevisionDeltaCommand command(
            long amountCent,
            DeliveryRevisionKind kind) {
        return new ApplyDeliveryRevisionDeltaCommand(
                "DO202608170001",
                UUID.fromString("20000000-0000-4000-8000-000000000001"),
                mock(DeliveryWalletEntryOwnerRef.class),
                mock(DeliveryRevisionWalletEntryRef.class),
                kind,
                amountCent,
                -100L,
                Instant.parse("2026-08-17T08:09:10.123Z"));
    }

    /**
     * 不连接数据库也让每条 SQL 真正经过参数数量校验，并记录事务中发生的写入。
     */
    private static final class RecordingJdbcTemplate extends JdbcTemplate {

        private final List<Invocation> invocations = new ArrayList<>();

        @Override
        public int update(String sql, Object... args) {
            requireMatchingPlaceholders(sql, args);
            invocations.add(new Invocation(
                    sql,
                    new ArrayList<>(java.util.Arrays.asList(args))));
            return 1;
        }

        @Override
        public <T> T queryForObject(
                String sql,
                Class<T> requiredType,
                Object... args) {
            requireMatchingPlaceholders(sql, args);
            if (requiredType == Long.class) {
                return requiredType.cast(900L);
            }
            throw new AssertionError("unexpected scalar query: " + sql);
        }

        @Override
        public <T> T queryForObject(
                String sql,
                RowMapper<T> rowMapper,
                Object... args) {
            requireMatchingPlaceholders(sql, args);
            ResultSet resultSet = mock(ResultSet.class);
            try {
                if (sql.contains("fund_organization_payout_account")) {
                    when(resultSet.getLong("id")).thenReturn(ACCOUNT_ID);
                    when(resultSet.getLong("available_payout_cent"))
                            .thenReturn(1_000L);
                    when(resultSet.getLong("frozen_withdrawal_cent"))
                            .thenReturn(20L);
                    when(resultSet.getLong("lock_version"))
                            .thenReturn(6L);
                } else if (sql.contains("FROM fund_payout_gate")) {
                    when(resultSet.getLong("merchant_profile_id"))
                            .thenReturn(500L);
                    when(resultSet.getString("gate_state"))
                            .thenReturn("OPEN");
                } else {
                    throw new AssertionError("unexpected row query: " + sql);
                }
                return rowMapper.mapRow(resultSet, 0);
            } catch (SQLException exception) {
                throw new AssertionError(exception);
            }
        }

        @Override
        public <T> List<T> query(
                String sql,
                RowMapper<T> rowMapper,
                Object... args) {
            requireMatchingPlaceholders(sql, args);
            ResultSet resultSet = mock(ResultSet.class);
            try {
                if (sql.contains(
                        "fund_organization_withdraw_config_head")) {
                    when(resultSet.getLong("current_config_id"))
                            .thenReturn(100L);
                    when(resultSet.getLong("current_version_no"))
                            .thenReturn(4L);
                } else if (sql.contains(
                        "FROM fund_organization_withdraw_config")) {
                    when(resultSet.getLong("id")).thenReturn(100L);
                    when(resultSet.getLong("version_no")).thenReturn(4L);
                    when(resultSet.getLong("hard_limit_cent"))
                            .thenReturn(20_000L);
                    when(resultSet.getBoolean("auto_withdrawal_enabled"))
                            .thenReturn(true);
                    when(resultSet.getLong("auto_min_cent"))
                            .thenReturn(10L);
                    when(resultSet.getLong("auto_max_cent"))
                            .thenReturn(200L);
                    when(resultSet.getLong(
                            "auto_review_free_threshold_cent"))
                            .thenReturn(80L);
                } else if (sql.contains(
                        "FROM iam_organization_user")) {
                    when(resultSet.getLong("id")).thenReturn(USER_ID);
                    when(resultSet.getLong("miniapp_channel_id"))
                            .thenReturn(200L);
                    when(resultSet.getLong("wechat_subject_id"))
                            .thenReturn(300L);
                    when(resultSet.getString("phone_e164"))
                            .thenReturn("+8613800000000");
                    when(resultSet.getString("status"))
                            .thenReturn("ACTIVE");
                    when(resultSet.getString("openid"))
                            .thenReturn("openid-test");
                    when(resultSet.getString("appid"))
                            .thenReturn("appid-test");
                } else if (sql.contains(
                        "SELECT gate.merchant_profile_id")) {
                    when(resultSet.getLong("merchant_profile_id"))
                            .thenReturn(500L);
                } else {
                    throw new AssertionError("unexpected list query: " + sql);
                }
                return List.of(rowMapper.mapRow(resultSet, 0));
            } catch (SQLException exception) {
                throw new AssertionError(exception);
            }
        }

        @Override
        public <T> List<T> query(String sql, RowMapper<T> rowMapper) {
            return query(sql, rowMapper, new Object[0]);
        }

        List<String> sql() {
            return invocations.stream().map(Invocation::sql).toList();
        }

        List<Object> argumentsFor(String fragment) {
            return invocations.stream()
                    .filter(invocation -> invocation.sql().contains(fragment))
                    .findFirst()
                    .orElseThrow()
                    .arguments();
        }

        private static void requireMatchingPlaceholders(
                String sql,
                Object[] arguments) {
            long placeholders = sql.chars().filter(value -> value == '?')
                    .count();
            assertThat(arguments)
                    .as("SQL placeholder count for %s", sql)
                    .hasSize(Math.toIntExact(placeholders));
        }
    }

    private record Invocation(String sql, List<Object> arguments) {
    }
}
