package org.enveloping.ecobin.identity.api.command;

import org.enveloping.ecobin.identity.api.persistence.OrganizationBootstrapPersistenceRef;

import java.time.Instant;
import java.util.Objects;

/**
 * 新机构创建成功后，在同一事务内初始化业务模块自有记录的命令。
 */
public record OrganizationBootstrapCommand(
        Instant organizationCreatedAt,
        OrganizationBootstrapPersistenceRef persistenceRef) {

    public OrganizationBootstrapCommand {
        Objects.requireNonNull(
                organizationCreatedAt, "organizationCreatedAt");
        Objects.requireNonNull(persistenceRef, "persistenceRef");
    }
}
