package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.TrustedInboxQuarantinePort;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;

@Service
public class TrustedDomainInboxQuarantineService
        implements TrustedInboxQuarantinePort {

    private final JdbcTemplate jdbc;
    private final CanonicalJson canonicalJson;
    private final ReliableOperationsJdbcRepository repository;

    public TrustedDomainInboxQuarantineService(
            JdbcTemplate jdbc,
            CanonicalJson canonicalJson,
            ReliableOperationsJdbcRepository repository) {
        this.jdbc = jdbc;
        this.canonicalJson = canonicalJson;
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID quarantine(
            TrustedOrganizationInboxRef sourceInbox,
            String reasonCode,
            String redactedDiagnostic) {
        if (reasonCode == null
                || !reasonCode.matches("[A-Z][A-Z0-9_]{0,39}")) {
            throw new IllegalArgumentException(
                    "reasonCode must be a stable safe code");
        }
        if (redactedDiagnostic == null
                || redactedDiagnostic.isBlank()
                || redactedDiagnostic.length() > 2000) {
            throw new IllegalArgumentException(
                    "redactedDiagnostic must be bounded text");
        }
        return sourceInbox.use(
                (inboxId, tenantId, organizationId) -> {
                    List<InboxEvidence> rows = jdbc.query("""
                                    SELECT
                                        source_namespace,
                                        source_principal_key,
                                        external_message_id,
                                        raw_transport_sha256,
                                        normalized_content_sha256
                                    FROM ops_inbox_message
                                    WHERE id = ?
                                      AND tenant_id = ?
                                      AND organization_id = ?
                                    FOR UPDATE
                                    """,
                            (rs, ignored) -> new InboxEvidence(
                                    rs.getString("source_namespace"),
                                    rs.getString(
                                            "source_principal_key"),
                                    rs.getString(
                                            "external_message_id"),
                                    rs.getBytes(
                                            "raw_transport_sha256"),
                                    rs.getBytes(
                                            "normalized_content_sha256")),
                            inboxId,
                            tenantId,
                            organizationId);
                    if (rows.size() != 1) {
                        throw new ReliableTaskInvariantException(
                                "domain quarantine source inbox is missing");
                    }
                    InboxEvidence evidence = rows.getFirst();
                    byte[] dedupe = canonicalJson.sha256LengthPrefixed(
                            evidence.sourceNamespace().getBytes(
                                    StandardCharsets.US_ASCII),
                            evidence.sourcePrincipalKey().getBytes(
                                    StandardCharsets.UTF_8),
                            evidence.externalMessageId().getBytes(
                                    StandardCharsets.UTF_8),
                            evidence.normalizedSha256(),
                            reasonCode.getBytes(
                                    StandardCharsets.US_ASCII));
                    return repository.upsertDomainQuarantine(
                            dedupe,
                            "ORGANIZATION",
                            tenantId,
                            organizationId,
                            evidence.sourceNamespace(),
                            evidence.sourcePrincipalKey(),
                            evidence.externalMessageId(),
                            inboxId,
                            evidence.rawSha256(),
                            evidence.normalizedSha256(),
                            reasonCode,
                            redactedDiagnostic,
                            repository.databaseNow());
                });
    }

    private record InboxEvidence(
            String sourceNamespace,
            String sourcePrincipalKey,
            String externalMessageId,
            byte[] rawSha256,
            byte[] normalizedSha256) {
    }
}
