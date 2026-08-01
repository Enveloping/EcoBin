package org.enveloping.ecobin.identity.application.deliveryorder;

import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException;
import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException.Reason;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRequestRef;
import org.enveloping.ecobin.identity.api.port.DeliveryWalletEntryOwnerResolverPort;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationUserRow;
import org.enveloping.ecobin.identity.application.persistence.DeliveryWalletEntryOwnerRefFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Map;
import java.util.Objects;
import java.util.Set;

@Service
public class DeliveryWalletEntryOwnerResolverService
        implements DeliveryWalletEntryOwnerResolverPort {

    private final DeliveryOrderIdentityRepository repository;
    private final DeliveryWalletEntryOwnerRefFactory referenceFactory;

    public DeliveryWalletEntryOwnerResolverService(
            DeliveryOrderIdentityRepository repository,
            DeliveryWalletEntryOwnerRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public DeliveryWalletEntryOwnerRef resolve(
            DeliveryWalletEntryOwnerRequestRef requestRef) {
        Objects.requireNonNull(requestRef, "requestRef");
        return requestRef.withRequestedOwnerOnce(this::resolveOwner);
    }

    private DeliveryWalletEntryOwnerRef resolveOwner(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        Map<Long, OrganizationUserRow> rows =
                repository.findOrganizationUsers(
                        Set.of(organizationUserId));
        OrganizationUserRow row = rows.get(organizationUserId);
        if (row == null
                || row.tenantId() != tenantId
                || row.organizationId() != organizationId
                || row.organizationUserId() != organizationUserId) {
            throw new DeliveryIdentityFactMismatchException(
                    Reason.ORGANIZATION_USER_MISMATCH,
                    "钱包明细关联的机构用户身份不存在或编号与公开 UUID 不匹配");
        }
        return referenceFactory.issue(
                row.tenantId(),
                row.organizationId(),
                row.organizationUserId(),
                row.organizationUserUid());
    }
}
