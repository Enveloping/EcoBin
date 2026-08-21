package org.enveloping.ecobin.recycling.application.walletquery;

import org.enveloping.ecobin.funds.api.port.WalletQueryPort;
import org.enveloping.ecobin.funds.api.query.PersonalWalletEntryAudience;
import org.enveloping.ecobin.funds.api.query.WalletEntryFilter;
import org.enveloping.ecobin.funds.api.result.WalletBalanceSnapshot;
import org.enveloping.ecobin.funds.api.result.WalletEntryItem;
import org.enveloping.ecobin.funds.api.result.WalletEntryPage;
import org.enveloping.ecobin.identity.api.port.MiniappWalletIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.WalletQueryAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.WalletScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletScope;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappWalletIdentity;
import org.enveloping.ecobin.recycling.web.v1.WalletModels;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.MiniappWalletEntry;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.OrganizationWalletEntry;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.PersonalWalletEntry;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.WalletSummary;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.UUID;

@Service
public class WalletReadApplicationService {

    private final MiniappWalletIdentityQueryPort miniappIdentity;
    private final WalletQueryAuthorizationPort webAuthorization;
    private final WalletQueryPort funds;
    private final PendingDeliveryRewardRepository pendingRewards;
    private final Clock clock;

    @Autowired
    public WalletReadApplicationService(
            MiniappWalletIdentityQueryPort miniappIdentity,
            WalletQueryAuthorizationPort webAuthorization,
            WalletQueryPort funds,
            PendingDeliveryRewardRepository pendingRewards) {
        this(
                miniappIdentity,
                webAuthorization,
                funds,
                pendingRewards,
                Clock.systemUTC());
    }

    WalletReadApplicationService(
            MiniappWalletIdentityQueryPort miniappIdentity,
            WalletQueryAuthorizationPort webAuthorization,
            WalletQueryPort funds,
            PendingDeliveryRewardRepository pendingRewards,
            Clock clock) {
        this.miniappIdentity = miniappIdentity;
        this.webAuthorization = webAuthorization;
        this.funds = funds;
        this.pendingRewards = pendingRewards;
        this.clock = clock;
    }

    @Transactional(
            readOnly = true,
            isolation = Isolation.REPEATABLE_READ)
    public WalletSummary miniappSummary() {
        CurrentMiniappWalletIdentity identity =
                miniappIdentity.current();
        long pendingCent = pendingRewards.pendingRewardCent(
                identity.pendingRewardOwnerRef());
        WalletBalanceSnapshot balance = funds.balance(
                identity.walletOwnerRef());
        return summary(balance, pendingCent);
    }

    @Transactional(
            readOnly = true,
            isolation = Isolation.REPEATABLE_READ)
    public WalletSummary webSummary(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid) {
        AuthorizedWalletScope authorized = webAuthorization.authorize(
                new WalletScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode,
                        organizationUserUid,
                        true));
        long pendingCent = pendingRewards.pendingRewardCent(
                authorized.pendingRewardOwnerRef());
        WalletBalanceSnapshot balance = funds.balance(
                authorized.walletOwnerRef());
        return summary(balance, pendingCent);
    }

    @Transactional(
            readOnly = true,
            isolation = Isolation.REPEATABLE_READ)
    public WalletModels.WalletEntryPage<MiniappWalletEntry> miniappEntries(
            String cursor,
            Integer limit) {
        CurrentMiniappWalletIdentity identity =
                miniappIdentity.current();
        WalletEntryPage page = funds.personalEntries(
                identity.walletOwnerRef(),
                PersonalWalletEntryAudience.ORDINARY_USER,
                cursor,
                limit);
        return new WalletModels.WalletEntryPage<>(
                page.items().stream()
                        .map(WalletReadApplicationService::miniappEntry)
                        .toList(),
                page.asOf(),
                page.nextCursor());
    }

    @Transactional(
            readOnly = true,
            isolation = Isolation.REPEATABLE_READ)
    public WalletModels.WalletEntryPage<PersonalWalletEntry>
            webPersonalEntries(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            String cursor,
            Integer limit) {
        AuthorizedWalletScope authorized = webAuthorization.authorize(
                new WalletScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode,
                        organizationUserUid));
        return personalPage(funds.personalEntries(
                authorized.walletOwnerRef(),
                PersonalWalletEntryAudience.AUDIT,
                cursor,
                limit));
    }

    @Transactional(
            readOnly = true,
            isolation = Isolation.REPEATABLE_READ)
    public WalletModels.WalletEntryPage<OrganizationWalletEntry>
            webOrganizationEntries(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            String entryType,
            Instant occurredFrom,
            Instant occurredTo,
            String sourceNo,
            String cursor,
            Integer limit) {
        AuthorizedWalletScope authorized = webAuthorization.authorize(
                new WalletScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode,
                        organizationUserUid));
        WalletEntryPage page = funds.organizationEntries(
                authorized.scopeRef(),
                new WalletEntryFilter(
                        entryType,
                        occurredFrom,
                        occurredTo,
                        sourceNo),
                cursor,
                limit);
        return new WalletModels.WalletEntryPage<>(
                page.items().stream()
                        .map(WalletReadApplicationService
                                ::organizationEntry)
                        .toList(),
                page.asOf(),
                page.nextCursor());
    }

    private WalletSummary summary(
            WalletBalanceSnapshot balance,
            long pendingCent) {
        return new WalletSummary(
                balance.walletVersion(),
                money(pendingCent),
                money(balance.availableBalanceCent()),
                money(balance.withdrawalProcessingCent()),
                clock.instant().truncatedTo(ChronoUnit.MILLIS));
    }

    private static WalletModels.WalletEntryPage<PersonalWalletEntry>
            personalPage(
            WalletEntryPage page) {
        return new WalletModels.WalletEntryPage<>(
                page.items().stream()
                        .map(WalletReadApplicationService::personalEntry)
                        .toList(),
                page.asOf(),
                page.nextCursor());
    }

    private static PersonalWalletEntry personalEntry(
            WalletEntryItem item) {
        return new PersonalWalletEntry(
                item.entryUid(),
                item.entrySequenceNo(),
                item.entryType(),
                money(item.availableDeltaCent()),
                money(item.processingDeltaCent()),
                money(item.availableBalanceAfterCent()),
                money(item.withdrawalProcessingAfterCent()),
                item.sourceType(),
                item.sourceNo(),
                item.occurredAt());
    }

    private static MiniappWalletEntry miniappEntry(
            WalletEntryItem item) {
        return new MiniappWalletEntry(
                item.entryUid(),
                item.entrySequenceNo(),
                item.entryType(),
                money(item.availableDeltaCent()),
                money(item.processingDeltaCent()),
                money(item.availableBalanceAfterCent()),
                money(item.withdrawalProcessingAfterCent()),
                item.sourceType(),
                item.sourceNo(),
                item.occurredAt());
    }

    private static OrganizationWalletEntry organizationEntry(
            WalletEntryItem item) {
        return new OrganizationWalletEntry(
                item.entryUid(),
                item.organizationUserUid().value(),
                item.entrySequenceNo(),
                item.entryType(),
                money(item.availableDeltaCent()),
                money(item.processingDeltaCent()),
                money(item.availableBalanceAfterCent()),
                money(item.withdrawalProcessingAfterCent()),
                item.sourceType(),
                item.sourceNo(),
                item.occurredAt());
    }

    private static String money(long cent) {
        return BigDecimal.valueOf(cent, 2).toPlainString();
    }
}
