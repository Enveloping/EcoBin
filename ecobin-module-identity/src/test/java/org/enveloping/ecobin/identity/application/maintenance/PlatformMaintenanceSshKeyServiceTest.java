package org.enveloping.ecobin.identity.application.maintenance;

import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.MaintenanceSshKeyPersistenceRef;
import org.enveloping.ecobin.identity.application.maintenance.MaintenanceSshKeyRepository.MaintenanceSshKeyRow;
import org.enveloping.ecobin.identity.application.maintenance.MaintenanceSshKeyRepository.PlatformAdminRow;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.CreateMaintenanceSshKeyRequest;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.RevokeMaintenanceSshKeyRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;

class PlatformMaintenanceSshKeyServiceTest {

    private static final long ADMIN_ID = 11;
    private static final UUID ADMIN_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID SESSION_UID =
            UUID.fromString("22222222-2222-4222-8222-222222222222");
    private static final Instant NOW =
            Instant.parse("2026-08-15T08:00:00Z");

    private InMemoryRepository repository;
    private RecordingAuditPort audit;
    private MaintenanceSshKeyPersistenceRef persistenceRef;
    private PlatformMaintenanceSshKeyService service;

    @BeforeEach
    void setUp() {
        repository = new InMemoryRepository();
        audit = new RecordingAuditPort();
        persistenceRef = mock(MaintenanceSshKeyPersistenceRef.class);
        service = new PlatformMaintenanceSshKeyService(
                repository,
                maintenanceSshKeyKey -> persistenceRef,
                audit,
                JsonMapper.builder().findAndAddModules().build());
        TargetWebActorContext.set(platformActor());
    }

    @AfterEach
    void tearDown() {
        TargetWebActorContext.clear();
    }

    @Test
    void createsMultipleActiveKeysAndExposesOnlyPublicMaterial() {
        String firstKey = Ed25519PublicKeyCodecTest.canonicalKey(1);
        var first = service.createOwnKey(
                UUID.randomUUID(),
                new CreateMaintenanceSshKeyRequest(
                        "  现场笔记本  ", firstKey));
        var second = service.createOwnKey(
                UUID.randomUUID(),
                new CreateMaintenanceSshKeyRequest(
                        "办公室电脑",
                        Ed25519PublicKeyCodecTest.canonicalKey(2)));

        assertEquals("现场笔记本", first.label());
        assertEquals("ACTIVE", first.status());
        assertTrue(first.fingerprintSha256().startsWith("SHA256:"));
        assertNotEquals(
                first.fingerprintSha256(), second.fingerprintSha256());
        assertEquals(2, service.listOwnKeys().size());
        var resolved = service.resolveActive(
                        ADMIN_UID, first.maintenanceSshKeyUid())
                .orElseThrow();
        assertEquals(firstKey, resolved.publicKey());
        assertSame(persistenceRef, resolved.persistenceRef());
        assertFalse(audit.entries.getFirst()
                .safeChangeSummaryJson().contains(firstKey));
    }

    @Test
    void exactCreateRetryReplaysWithoutASecondInsert() {
        UUID operationUid = UUID.randomUUID();
        var request = new CreateMaintenanceSshKeyRequest(
                "laptop",
                Ed25519PublicKeyCodecTest.canonicalKey(4));

        var first = service.createOwnKey(operationUid, request);
        var replayed = service.createOwnKey(operationUid, request);

        assertEquals(first, replayed);
        assertEquals(1, repository.insertCount);
        assertEquals(1, audit.entries.size());
    }

    @Test
    void changedRequestCannotReuseAnIdempotencyKey() {
        UUID operationUid = UUID.randomUUID();
        String publicKey = Ed25519PublicKeyCodecTest.canonicalKey(5);
        service.createOwnKey(
                operationUid,
                new CreateMaintenanceSshKeyRequest("one", publicKey));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.createOwnKey(
                        operationUid,
                        new CreateMaintenanceSshKeyRequest(
                                "two", publicKey)));

        assertEquals(409, failure.status());
        assertEquals("COMMON.IDEMPOTENCY_KEY_CONFLICT", failure.code());
    }

    @Test
    void revocationIsPermanentIdempotentAndRemovesActiveResolution() {
        String publicKey = Ed25519PublicKeyCodecTest.canonicalKey(6);
        var created = service.createOwnKey(
                UUID.randomUUID(),
                new CreateMaintenanceSshKeyRequest("laptop", publicKey));
        UUID revokeOperationUid = UUID.randomUUID();
        var request = new RevokeMaintenanceSshKeyRequest(
                created.version(), "电脑遗失");

        var revoked = service.revokeOwnKey(
                revokeOperationUid,
                created.maintenanceSshKeyUid(),
                request);
        var replayed = service.revokeOwnKey(
                revokeOperationUid,
                created.maintenanceSshKeyUid(),
                request);

        assertEquals("REVOKED", revoked.status());
        assertEquals(created.version() + 1, revoked.version());
        assertEquals(revoked, replayed);
        assertTrue(service.resolveActive(
                ADMIN_UID, created.maintenanceSshKeyUid()).isEmpty());

        TargetApiException duplicate = assertThrows(
                TargetApiException.class,
                () -> service.createOwnKey(
                        UUID.randomUUID(),
                        new CreateMaintenanceSshKeyRequest(
                                "replacement label", publicKey)));
        assertEquals(
                "IDENTITY.MAINTENANCE_SSH_KEY_ALREADY_REGISTERED",
                duplicate.code());
    }

    @Test
    void staleVersionAndAnotherAdministratorsKeyAreRejected() {
        var created = service.createOwnKey(
                UUID.randomUUID(),
                new CreateMaintenanceSshKeyRequest(
                        "laptop",
                        Ed25519PublicKeyCodecTest.canonicalKey(8)));

        TargetApiException stale = assertThrows(
                TargetApiException.class,
                () -> service.revokeOwnKey(
                        UUID.randomUUID(),
                        created.maintenanceSshKeyUid(),
                        new RevokeMaintenanceSshKeyRequest(
                                99L, "stale")));
        assertEquals("COMMON.VERSION_CONFLICT", stale.code());

        UUID foreignUid = UUID.randomUUID();
        repository.rows.add(new MaintenanceSshKeyRow(
                99,
                foreignUid,
                99,
                "foreign",
                Ed25519PublicKeyCodecTest.canonicalKey(9),
                "SHA256:foreign",
                null,
                null,
                0,
                NOW,
                NOW));
        TargetApiException foreign = assertThrows(
                TargetApiException.class,
                () -> service.revokeOwnKey(
                        UUID.randomUUID(),
                        foreignUid,
                        new RevokeMaintenanceSshKeyRequest(
                                0L, "not mine")));
        assertEquals(404, foreign.status());
    }

    @Test
    void disabledAdministratorAndNonPlatformActorCannotSelfManage() {
        repository.admin = new PlatformAdminRow(
                ADMIN_ID, ADMIN_UID, false, 7, null);
        TargetApiException disabled = assertThrows(
                TargetApiException.class,
                service::listOwnKeys);
        assertEquals("AUTH.SESSION_INVALID", disabled.code());

        TargetWebActorContext.set(new TargetWebActor(
                WebAccountType.STAFF,
                TrustedAudience.WEB_STAFF,
                ADMIN_ID,
                ADMIN_UID,
                1L,
                "tenant",
                "tenant",
                SESSION_UID,
                "staff",
                null,
                0,
                7,
                Instant.parse("2030-01-01T00:00:00Z"),
                Set.of(),
                List.of()));
        TargetApiException staff = assertThrows(
                TargetApiException.class,
                service::listOwnKeys);
        assertEquals(403, staff.status());
    }

    @Test
    void activeLookupAlsoStopsWhenAdministratorIsDisabled() {
        var created = service.createOwnKey(
                UUID.randomUUID(),
                new CreateMaintenanceSshKeyRequest(
                        "laptop",
                        Ed25519PublicKeyCodecTest.canonicalKey(10)));
        assertTrue(service.resolveActive(
                ADMIN_UID, created.maintenanceSshKeyUid()).isPresent());

        repository.admin = new PlatformAdminRow(
                ADMIN_ID, ADMIN_UID, false, 7, null);

        assertTrue(service.resolveActive(
                ADMIN_UID, created.maintenanceSshKeyUid()).isEmpty());
    }

    private static TargetWebActor platformActor() {
        return new TargetWebActor(
                WebAccountType.PLATFORM_ADMIN,
                TrustedAudience.WEB_PLATFORM,
                ADMIN_ID,
                ADMIN_UID,
                null,
                null,
                null,
                SESSION_UID,
                "平台管理员",
                null,
                0,
                7,
                Instant.parse("2030-01-01T00:00:00Z"),
                Set.of(),
                List.of());
    }

    private static final class InMemoryRepository
            implements MaintenanceSshKeyRepository {

        private PlatformAdminRow admin = new PlatformAdminRow(
                ADMIN_ID, ADMIN_UID, true, 7, null);
        private final List<MaintenanceSshKeyRow> rows = new ArrayList<>();
        private int insertCount;

        @Override
        public Optional<PlatformAdminRow> findPlatformAdmin(
                long platformAdminId,
                UUID platformAdminUid,
                boolean forUpdate) {
            return admin.id() == platformAdminId
                    && admin.uid().equals(platformAdminUid)
                    ? Optional.of(admin) : Optional.empty();
        }

        @Override
        public List<MaintenanceSshKeyRow> findAll(long platformAdminId) {
            return rows.stream()
                    .filter(row -> row.platformAdminId() == platformAdminId)
                    .toList();
        }

        @Override
        public Optional<MaintenanceSshKeyRow> findByUid(
                long platformAdminId,
                UUID maintenanceSshKeyUid,
                boolean forUpdate) {
            return rows.stream()
                    .filter(row -> row.platformAdminId() == platformAdminId)
                    .filter(row -> row.uid().equals(maintenanceSshKeyUid))
                    .findFirst();
        }

        @Override
        public Optional<MaintenanceSshKeyRow> findByFingerprint(
                long platformAdminId,
                String fingerprintSha256) {
            return rows.stream()
                    .filter(row -> row.platformAdminId() == platformAdminId)
                    .filter(row -> row.fingerprintSha256()
                            .equals(fingerprintSha256))
                    .findFirst();
        }

        @Override
        public Optional<MaintenanceSshKeyRow> findActive(
                UUID platformAdminUid,
                UUID maintenanceSshKeyUid,
                boolean forUpdate) {
            if (!admin.uid().equals(platformAdminUid)
                    || !admin.enabled()
                    || admin.deletedAt() != null) {
                return Optional.empty();
            }
            return findByUid(admin.id(), maintenanceSshKeyUid, false)
                    .filter(row -> row.revokedAt() == null);
        }

        @Override
        public void insert(
                UUID maintenanceSshKeyUid,
                long platformAdminId,
                String label,
                String publicKey,
                String fingerprintSha256) {
            insertCount++;
            rows.add(new MaintenanceSshKeyRow(
                    rows.size() + 1L,
                    maintenanceSshKeyUid,
                    platformAdminId,
                    label,
                    publicKey,
                    fingerprintSha256,
                    null,
                    null,
                    0,
                    NOW,
                    NOW));
        }

        @Override
        public int revoke(
                long keyId,
                long expectedVersion,
                String reason) {
            for (int index = 0; index < rows.size(); index++) {
                MaintenanceSshKeyRow row = rows.get(index);
                if (row.id() == keyId
                        && row.version() == expectedVersion
                        && row.revokedAt() == null) {
                    rows.set(index, new MaintenanceSshKeyRow(
                            row.id(),
                            row.uid(),
                            row.platformAdminId(),
                            row.label(),
                            row.publicKey(),
                            row.fingerprintSha256(),
                            NOW.plusSeconds(1),
                            reason,
                            row.version() + 1,
                            row.createdAt(),
                            NOW.plusSeconds(1)));
                    return 1;
                }
            }
            return 0;
        }
    }

    private static final class RecordingAuditPort implements AuditPort {

        private final List<AuditEntry> entries = new ArrayList<>();
        private final Map<UUID, SuccessfulAudit> successful =
                new HashMap<>();

        @Override
        public Optional<SuccessfulAudit> findSuccessful(UUID operationUid) {
            return Optional.ofNullable(successful.get(operationUid));
        }

        @Override
        public long append(AuditEntry entry) {
            entries.add(entry);
            successful.put(entry.operationUid(), new SuccessfulAudit(
                    entry.operationUid(),
                    entry.actorKind(),
                    entry.platformAdminId(),
                    entry.staffAccountId(),
                    entry.organizationUserId(),
                    entry.scopeKind(),
                    entry.tenantId(),
                    entry.organizationId(),
                    entry.actionCode(),
                    entry.targetType(),
                    entry.targetStableKey(),
                    entry.safeChangeSummaryJson()));
            return entries.size();
        }
    }
}
