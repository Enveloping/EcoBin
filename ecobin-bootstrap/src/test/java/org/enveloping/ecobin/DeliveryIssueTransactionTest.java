package org.enveloping.ecobin;

import org.enveloping.ecobin.device.application.delivery.NativeDeliveryIssueService;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.enveloping.ecobin.device.application.target.TrustedPlatformDeviceAssetFactService;
import org.enveloping.ecobin.device.application.target.ReliablePlatformEdgeConfirmationService;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.framework.reliability.*;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.ClassPathBeanDefinitionScanner;
import org.springframework.core.type.filter.AssignableTypeFilter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.UUID;
import java.util.Map;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.CountDownLatch;
import org.enveloping.ecobin.device.api.result.NativeDeliveryIssueEvidence;
import org.enveloping.ecobin.device.application.delivery.TrustedDeliveryCompletionService;
import org.enveloping.ecobin.device.application.delivery.DeliveryCompletionFactsRefFactory;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

import static org.junit.jupiter.api.Assertions.*;

/** Real MySQL transaction and migration CHECK/FKs with minimal parent fixtures; no production connection. */
@EnabledIfEnvironmentVariable(named="ECOBIN_ISSUE_MYSQL_URL",
        matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_issue_p1bk(?:\\?.*)?")
class DeliveryIssueTransactionTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private SingleConnectionDataSource dataSource;
    private JdbcTemplate jdbc;
    private TransactionTemplate tx;
    private NativeDeliveryIssueService service;
    private ObjectNode normalized;
    private ObjectNode archiveEvent;
    private Path projectRoot;
    private boolean failProof;
    private boolean failConfirmation;
    private static final List<String> TABLES=List.of("dev_delivery_issue_evidence","dev_delivery_issue_part","dev_delivery_issue",
        "p1bk_effect","rec_delivery_order","dev_physical_result","dev_device_occupancy","dev_device_command",
        "dev_port","dev_delivery_session","dev_device_runtime_state","dev_device_asset","ops_inbox_message","ops_reliable_task");

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_ISSUE_MYSQL_URL"),"root","");
        assertEquals("ecobin_issue_p1bk",connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        dataSource = new SingleConnectionDataSource(connection,true);
        jdbc = new JdbcTemplate(dataSource);
        dropTables();
        tx = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isDirectory(root.resolve("contracts"))) root=root.getParent();
        projectRoot=root;
        ObjectNode event = (ObjectNode)mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/delivery-issue-archived.event.json")));
        archiveEvent=event.deepCopy();
        normalized=mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName","SN-CONTRACT-0001");
        normalized.set("event",event);
        jdbc.execute("CREATE TABLE dev_device_asset(id BIGINT PRIMARY KEY, hardware_sn VARCHAR(64) UNIQUE)");
        jdbc.execute("CREATE TABLE dev_delivery_session(id BIGINT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT, asset_id BIGINT, session_uid CHAR(36), port_id BIGINT, device_config_version_no BIGINT, device_config_content_sha256 BINARY(32), status VARCHAR(32), device_completed_at DATETIME(3), ended_at DATETIME(3), end_reason VARCHAR(64), lock_version BIGINT, updated_at DATETIME(3), UNIQUE(tenant_id,organization_id,asset_id,id))");
        jdbc.execute("CREATE TABLE dev_port(id BIGINT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT, asset_id BIGINT, port_no INT)");
        jdbc.execute("CREATE TABLE dev_device_command(id BIGINT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT, asset_id BIGINT, command_uid CHAR(36), delivery_session_id BIGINT, command_type VARCHAR(40), semantic_payload JSON, semantic_payload_sha256 BINARY(32), UNIQUE(tenant_id,organization_id,asset_id,id))");
        jdbc.execute("CREATE TABLE dev_device_occupancy(asset_id BIGINT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT, occupancy_kind VARCHAR(32), delivery_session_id BIGINT)");
        jdbc.execute("CREATE TABLE dev_physical_result(command_id BIGINT)");
        jdbc.execute("CREATE TABLE rec_delivery_order(delivery_session_id BIGINT)");
        jdbc.execute("CREATE TABLE p1bk_effect(kind VARCHAR(32), target_key CHAR(36))");
        jdbc.execute("CREATE TABLE ops_inbox_message(id BIGINT PRIMARY KEY)");
        jdbc.execute("INSERT INTO ops_inbox_message WITH RECURSIVE n AS(SELECT 1 AS v UNION ALL SELECT v+1 FROM n WHERE v<600) SELECT v FROM n");
        // Execute the unmodified production migration, including its real foreign/unique keys and CHECKs.
        // Parent fixtures remain minimal: this is not a complete V1..V79 database or funds integration.
        String migration = Files.readString(root.resolve("ecobin-bootstrap/src/main/resources/db/p0-migration/V79__native_delivery_issue.sql"));
        for(String statement:migration.split(";")) {
            int at=statement.indexOf("CREATE TABLE "); if(at<0)continue;
            jdbc.execute(statement.substring(at));
        }
        var payload=event.path("payload");
        ObjectNode cloud=(ObjectNode)mapper.readTree(Files.readString(root.resolve("contracts/examples/onenet/start-delivery-session.command.json")));
        jdbc.update("INSERT INTO dev_device_asset VALUES(10,'SN-CONTRACT-0001')");
        jdbc.update("INSERT INTO dev_port VALUES(20,1,2,10,2)");
        jdbc.update("INSERT INTO dev_delivery_session VALUES(30,1,2,10,?,20,8,?,'IN_PROGRESS',NULL,NULL,NULL,0,NOW(3))",
                payload.path("sessionUid").asText(),HexFormat.of().parseHex("a".repeat(64)));
        jdbc.update("INSERT INTO dev_device_command VALUES(40,1,2,10,?,30,'START_DELIVERY_SESSION',?,?)",
                payload.path("originalCommandUid").asText(),mapper.writeValueAsString(cloud.path("payload")),
                HexFormat.of().parseHex(payload.path("originalCommandPayloadSha256").asText()));
        jdbc.update("INSERT INTO dev_device_occupancy VALUES(10,1,2,'DELIVERY',30)");
        service=serviceFor(jdbc);
    }
    private NativeDeliveryIssueService serviceFor(JdbcTemplate database){
        return new NativeDeliveryIssueService(database,mapper,new DeviceConfigurationCanonicalizer(),new ReliableDeviceTaskProofPort(){
            public void completeFromTrustedProof(String type,String target,String key){
                assertEquals("START_DELIVERY_SESSION",type); assertEquals("DELIVERY_SESSION",target);
                database.update("INSERT INTO p1bk_effect VALUES('PROOF',?)",key);
                if(failProof) throw new IllegalStateException("injected proof failure");
            }
            public void completeDispatchFromTrustedCommandObservation(UUID command){throw new AssertionError("not command observation");}
        });
    }
    private void dropTables(){for(String table:TABLES)jdbc.execute("DROP TABLE IF EXISTS "+table);}
    @AfterEach void close(){try{if(jdbc!=null)dropTables();}finally{if(dataSource!=null)dataSource.destroy();}}
    private NativeDeliveryIssueService.ApplyResult apply(){
        return tx.execute(s->service.applyTrusted(100,normalized,LocalDateTime.of(2026,9,13,12,0)));
    }
    private int count(String table){return jdbc.queryForObject("SELECT COUNT(*) FROM "+table,Integer.class);}

    @Test void archiveClosesOnlyOriginalLogicalWorkAndDuplicateHasNoBusinessEffects(){
        assertTrue(apply().changed()); assertFalse(apply().changed());
        assertEquals(1,count("dev_delivery_issue")); assertEquals(1,count("p1bk_effect"));
        assertEquals(0,count("dev_device_occupancy"));
        assertEquals("DEVICE_ABORTED",jdbc.queryForObject("SELECT status FROM dev_delivery_session",String.class));
        assertEquals("MCU_RESTART_FINAL_RESULT_UNAVAILABLE",jdbc.queryForObject("SELECT end_reason FROM dev_delivery_session",String.class));
        assertEquals(0,count("rec_delivery_order")); assertEquals(0,count("dev_physical_result"));
    }

    @ParameterizedTest @ValueSource(booleans={false,true})
    void trustedPlatformHandlerArchivesAndConfirmsInOneTransaction(boolean failure){
        failConfirmation=failure;
        try(var context=new AnnotationConfigApplicationContext()) {
            var scanner=new ClassPathBeanDefinitionScanner(context,false);
            scanner.addIncludeFilter(new AssignableTypeFilter(TrustedPlatformInboxRefFactory.class));
            scanner.addIncludeFilter(new AssignableTypeFilter(PlatformDeviceAssetTaskRefFactory.class));
            scanner.scan("org.enveloping.ecobin.framework.reliability"); context.refresh();
            jdbc.execute("CREATE TABLE ops_reliable_task(task_key VARCHAR(128))");
            normalized.put("eventCanonicalSha256","f".repeat(64));
            var confirmation=new ReliablePlatformEdgeConfirmationService(mapper,jdbc,new DeviceConfigurationCanonicalizer(),
                context.getBean(PlatformDeviceAssetTaskRefFactory.class),new ReliablePlatformDeviceControlTaskRegistrationPort(){
                    public UUID register(ReliablePlatformDeviceControlTaskRegistration registration){
                        jdbc.update("INSERT INTO p1bk_effect VALUES('CONFIRMATION',NULL)");
                        if(failConfirmation)throw new IllegalStateException("injected confirmation failure");
                        return UUID.randomUUID();
                    }
                });
            var handler=new TrustedPlatformDeviceAssetFactService(jdbc,mapper,confirmation,null,null,null,null,null,null,service);
            java.util.function.Supplier<TrustedDeviceEventApplyResult> run=()->tx.execute(s->handler.apply(new TrustedPlatformDeviceAssetFactEvent(
                context.getBean(TrustedPlatformInboxRefFactory.class).issue(100),NativeDeliveryIssueService.ARCHIVE_EVENT,2,
                mapper.writeValueAsString(normalized))));
            if(failure){
                assertThrows(IllegalStateException.class,run::get);assertEquals(0,count("dev_delivery_issue"));
                assertEquals(0,count("p1bk_effect"));assertEquals(1,count("dev_device_occupancy"));
            }else{
                assertEquals(TrustedDeviceEventApplyResult.APPLIED,run.get());
                assertEquals(1,count("dev_delivery_issue")); assertEquals(2,count("p1bk_effect"));
            }
        }
    }

    @Test void simultaneousArchiveConnectionsProduceOneVerdictAndOneTaskEffect() throws Exception {
        var otherSource=new DriverManagerDataSource(System.getenv("ECOBIN_ISSUE_MYSQL_URL"),"root","");
        var other=new JdbcTemplate(otherSource);var otherTx=new TransactionTemplate(new DataSourceTransactionManager(otherSource));
        var otherService=serviceFor(other);var start=new CountDownLatch(1);
        try(var pool=Executors.newFixedThreadPool(2)) {
            var first=pool.submit(()->{start.await();return apply();});
            var second=pool.submit(()->{start.await();return otherTx.execute(s->otherService.applyTrusted(100,normalized,LocalDateTime.now()));});
            start.countDown();assertNotEquals(first.get().changed(),second.get().changed());
        }
        assertEquals(1,count("dev_delivery_issue"));assertEquals(1,count("p1bk_effect"));assertEquals(0,count("dev_device_occupancy"));
    }

    @Test void actualNormalCompletionHandlerCannotSettleAnArchivedDelivery() throws Exception {
        assertTrue(apply().changed());
        jdbc.execute("ALTER TABLE dev_device_asset ADD tenant_id BIGINT DEFAULT 1,ADD organization_id BIGINT DEFAULT 2");
        jdbc.execute("CREATE TABLE dev_device_runtime_state(asset_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT)");
        jdbc.update("INSERT INTO dev_device_runtime_state VALUES(10,1,2)");
        jdbc.execute("""
                ALTER TABLE dev_delivery_session
                ADD organization_user_id BIGINT,ADD device_config_version_id BIGINT,
                ADD device_config_mcu_payload_sha256 BINARY(32),ADD port_config_snapshot_id BIGINT,
                ADD delivery_config_version_id BIGINT,ADD delivery_config_content_sha256 BINARY(32),
                ADD bag_id BIGINT,ADD bag_uid_snapshot CHAR(36),ADD bag_code_snapshot VARCHAR(32),
                ADD unit_price_yuan_per_kg DECIMAL(10,4),ADD open_balance_floor_cent BIGINT,
                ADD max_review_abs_weight_g BIGINT,ADD negative_weight_anomaly_threshold_g BIGINT
                """);
        jdbc.update("UPDATE dev_delivery_session SET bag_uid_snapshot='50000000-0000-4000-8000-000000000001'");
        jdbc.update("INSERT INTO dev_device_occupancy VALUES(10,1,2,'CLEAN',31)");
        try(var context=new AnnotationConfigApplicationContext()) {
            var scanner=new ClassPathBeanDefinitionScanner(context,false);
            for(var type:List.of(TrustedOrganizationInboxRefFactory.class,DeliveryCompletionFactsRefFactory.class,DeviceAssetTaskRefFactory.class))
                scanner.addIncludeFilter(new AssignableTypeFilter(type));
            scanner.scan("org.enveloping.ecobin.framework.reliability","org.enveloping.ecobin.device.api.persistence");context.refresh();
            var confirmations=new ReliableEdgeConfirmationService(mapper,jdbc,new DeviceConfigurationCanonicalizer(),context.getBean(DeviceAssetTaskRefFactory.class),
                new ReliableDeviceControlTaskRegistrationPort(){
                    public UUID register(ReliableDeviceControlTaskRegistration r){jdbc.update("INSERT INTO p1bk_effect VALUES('NEGATIVE_CONFIRMATION',NULL)");return UUID.randomUUID();}
                    public void cancelPending(DeviceAssetTaskRef r,String t,String target,String key){throw new AssertionError("no cancellation");}
                });
            var source=context.getBean(TrustedOrganizationInboxRefFactory.class);
            var normal=new TrustedDeliveryCompletionService(jdbc,mapper,context.getBean(DeliveryCompletionFactsRefFactory.class),confirmations,
                new ReliableDeviceTaskProofPort(){
                    public void completeFromTrustedProof(String a,String b,String c){throw new AssertionError("must not complete normal result");}
                    public void completeDispatchFromTrustedCommandObservation(UUID uid){throw new AssertionError("not a command observation");}
                },(inbox,reason,diagnostic)->{jdbc.update("INSERT INTO p1bk_effect VALUES('QUARANTINE',NULL)");return UUID.randomUUID();},source);
            normalized.set("event",mapper.readTree(Files.readString(projectRoot.resolve("contracts/examples/onenet/delivery-complete.event.json"))));
            normalized.put("eventCanonicalSha256","f".repeat(64));
            assertEquals(TrustedDeviceEventApplyResult.QUARANTINED,tx.execute(s->normal.complete(new TrustedDeviceInboxEvent(
                    source.issue(102,1,2),"DELIVERY_COMPLETE",2,mapper.writeValueAsString(normalized)),facts->{
                throw new AssertionError("archived delivery reached order/wallet business writer");
            })));
        }
        assertEquals(0,count("rec_delivery_order"));assertEquals(0,count("dev_physical_result"));
        assertEquals("DEVICE_ABORTED",jdbc.queryForObject("SELECT status FROM dev_delivery_session",String.class));
        assertEquals("CLEAN",jdbc.queryForObject("SELECT occupancy_kind FROM dev_device_occupancy",String.class));
        assertEquals(1,count("dev_delivery_issue"));
    }

    @Test void taskFailureRollsBackVerdictSessionAndOriginalOccupancy(){
        failProof=true;
        assertThrows(IllegalStateException.class,this::apply);
        assertEquals(0,count("dev_delivery_issue")); assertEquals(0,count("p1bk_effect"));
        assertEquals(1,count("dev_device_occupancy"));
        assertEquals("IN_PROGRESS",jdbc.queryForObject("SELECT status FROM dev_delivery_session",String.class));
        failProof=false; assertTrue(apply().changed());
    }

    @Test void completeEvidenceIsLinkedWithoutReopeningArchivedDelivery() throws Exception {
        assertTrue(apply().changed());
        Path root=Path.of("").toAbsolutePath(); if(!Files.isDirectory(root.resolve("contracts")))root=root.getParent();
        normalized.set("event",mapper.readTree(Files.readString(root.resolve("contracts/examples/onenet/delivery-issue-evidence-appended.event.json"))));
        assertTrue(tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.of(2026,9,13,12,1))).changed());
        assertFalse(tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.of(2026,9,13,12,2))).changed());
        assertEquals(1,count("dev_delivery_issue_evidence")); assertEquals(1,count("dev_delivery_issue_part"));
        assertEquals(1,count("p1bk_effect")); assertEquals(0,count("rec_delivery_order"));
        assertEquals("DEVICE_ABORTED",jdbc.queryForObject("SELECT status FROM dev_delivery_session",String.class));
    }

    @Test void lateCompleteOriginalPacketOnlyAddsEvidenceEvenAfterLaterWorkStarts() throws Exception {
        assertTrue(apply().changed());
        jdbc.update("INSERT INTO dev_device_occupancy VALUES(10,1,2,'CLEAN',31)");
        byte[] result=originalResult();
        normalized.set("event",fragment(result,"FINAL_RESULT",0,1));
        assertTrue(tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.now())).changed());
        assertFalse(tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.now())).changed());
        assertEquals(1,count("dev_delivery_issue_evidence"));assertEquals(1,count("p1bk_effect"));
        assertEquals(0,count("dev_physical_result"));assertEquals(0,count("rec_delivery_order"));
        assertEquals("DEVICE_ABORTED",jdbc.queryForObject("SELECT status FROM dev_delivery_session",String.class));
        assertEquals("CLEAN",jdbc.queryForObject("SELECT occupancy_kind FROM dev_device_occupancy",String.class));
        assertArrayEquals(result,jdbc.queryForObject("SELECT part_bytes FROM dev_delivery_issue_part",byte[].class));
    }

    @ParameterizedTest @ValueSource(strings={"work","boot","command","sequence","config","port","digest","length"})
    void wrongLatePacketIsNotAssociatedWithOriginalIssue(String defect) throws Exception {
        assertTrue(apply().changed());byte[] raw=originalResult();var b=ByteBuffer.wrap(raw);
        switch(defect){
            case "work" -> b.putLong(12,123);
            case "boot" -> {b.putLong(0,99);b.putLong(124,99);b.putLong(170,99);}
            case "command" -> b.putLong(70,123);
            case "sequence" -> b.putInt(86,7);
            case "config" -> b.putLong(62,9);
            case "port" -> raw[61]=3;
            case "digest" -> raw[28]^=1;
            case "length" -> raw=Arrays.copyOf(raw,198);
        }
        if(!defect.equals("digest")&&!defect.equals("length"))signResult(raw);
        normalized.set("event",fragment(raw,"FINAL_RESULT",0,1));
        assertThrows(UntrustedInboxSourceException.class,()->tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.now())));
        assertEquals(0,count("dev_delivery_issue_evidence"));assertEquals(0,count("dev_delivery_issue_part"));
        assertEquals(1,count("p1bk_effect"));assertEquals(0,count("rec_delivery_order"));
    }

    @Test void reversedPartsAreVerifiedOnlyWhenWholeOriginalEvidenceHasArrived(){
        byte[] context="原始称重与动作记录".repeat(200).getBytes(StandardCharsets.UTF_8);
        ((ObjectNode)normalized.path("event").path("payload")).put("archiveEvidenceSha256",hash(context));
        resign();archiveEvent=((ObjectNode)normalized.path("event")).deepCopy();assertTrue(apply().changed());
        int parts=(context.length+255)/256;
        for(int i=parts;i>=1;i--){
            normalized.set("event",fragment(context,"ARCHIVE_CONTEXT",0,i));int inbox=200+i;
            assertTrue(tx.execute(s->service.applyTrusted(inbox,normalized,LocalDateTime.now())).changed());
            assertEquals(i==1?1:0,count("dev_delivery_issue_evidence"));
        }
        assertEquals(parts,count("dev_delivery_issue_part"));assertEquals(1,count("p1bk_effect"));
        assertEquals(0,count("rec_delivery_order"));
    }

    @Test void fragmentBeforeArchiveIsRetryableAndCanBeAppliedAfterHeaderArrives(){
        byte[] bytes="{\"diagnosticOnly\":true}".getBytes(StandardCharsets.UTF_8);
        var piece=fragment(bytes,"ARCHIVE_CONTEXT",0,1);normalized.set("event",piece);
        assertThrows(IllegalStateException.class,()->tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.now())));
        assertEquals(0,count("dev_delivery_issue_part"));
        normalized.set("event",archiveEvent);assertTrue(apply().changed());normalized.set("event",piece);
        assertTrue(tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.now())).changed());
    }

    @ParameterizedTest @ValueSource(strings={"samePart","wholeDigest"})
    void corruptOrConflictingPieceCannotReplaceOriginalOrMarkEvidenceComplete(String defect){
        byte[] raw=new byte[300];Arrays.fill(raw,(byte)42);
        ((ObjectNode)normalized.path("event").path("payload")).put("archiveEvidenceSha256",hash(raw));
        resign();archiveEvent=((ObjectNode)normalized.path("event")).deepCopy();assertTrue(apply().changed());
        normalized.set("event",fragment(raw,"ARCHIVE_CONTEXT",0,1));
        assertTrue(tx.execute(s->service.applyTrusted(101,normalized,LocalDateTime.now())).changed());
        byte[] original=jdbc.queryForObject("SELECT part_bytes FROM dev_delivery_issue_part",byte[].class);
        normalized.set("event",fragment(raw,"ARCHIVE_CONTEXT",0,defect.equals("samePart")?1:2));
        var p=(ObjectNode)normalized.path("event").path("payload");String data=p.path("dataHex").asText();p.put("dataHex","00"+data.substring(2));resign();
        assertThrows(UntrustedInboxSourceException.class,()->tx.execute(s->service.applyTrusted(102,normalized,LocalDateTime.now())));
        assertEquals(1,count("dev_delivery_issue_part"));assertEquals(0,count("dev_delivery_issue_evidence"));
        assertArrayEquals(original,jdbc.queryForObject("SELECT part_bytes FROM dev_delivery_issue_part",byte[].class));
    }

    @ParameterizedTest @ValueSource(strings={"tenant_id=999","source_inbox_id=9999","source_mcu_boot_id=target_mcu_boot_id","business_value='MONEY'","reason='NORMAL'"})
    void migrationRejectsCrossScopeParentsAndValueBearingVerdicts(String assignment){
        assertTrue(apply().changed());
        assertThrows(org.springframework.dao.DataAccessException.class,()->jdbc.update("UPDATE dev_delivery_issue SET "+assignment));
        assertEquals("NONE",jdbc.queryForObject("SELECT business_value FROM dev_delivery_issue",String.class));
        assertEquals(1,count("dev_delivery_issue"));
    }

    private byte[] originalResult() throws Exception {
        var vectors=mapper.readTree(Files.readString(projectRoot.resolve("contracts/examples/uart/golden-vectors.json"))).path("vectors");
        byte[] raw=null;for(var vector:vectors)if(vector.path("name").asText().equals("work_result_delivery"))raw=HexFormat.of().parseHex(vector.path("payloadHex").asText());
        assertNotNull(raw);var b=ByteBuffer.wrap(raw);var p=archiveEvent.path("payload");
        b.putLong(0,101);putUuid(b,12,p.path("sessionUid").asText());b.putLong(62,8);raw[61]=2;
        putUuid(b,70,"32000000-0000-4000-8000-000000000002");b.putInt(86,6);b.putLong(124,101);b.putLong(170,101);
        signResult(raw);return raw;
    }
    private static void putUuid(ByteBuffer b,int at,String value){UUID u=UUID.fromString(value);b.putLong(at,u.getMostSignificantBits());b.putLong(at+8,u.getLeastSignificantBits());}
    private static void signResult(byte[] raw){
        byte[] domain="ECOBIN:UART:WORK-RESULT:v2\0".getBytes(StandardCharsets.US_ASCII);
        var bytes=ByteBuffer.allocate(domain.length+3+167);bytes.put(domain).put((byte)64).putShort((short)199).put(raw,0,28).put(raw,60,139);
        System.arraycopy(NativeDeliveryIssueEvidence.sha256(bytes.array()),0,raw,28,32);
    }
    private static String hash(byte[] bytes){return HexFormat.of().formatHex(NativeDeliveryIssueEvidence.sha256(bytes));}
    private ObjectNode fragment(byte[] bytes,String kind,long index,int part){
        ObjectNode event=archiveEvent.deepCopy();var h=archiveEvent.path("payload");
        event.put("eventType",NativeDeliveryIssueService.EVIDENCE_EVENT).put("eventUid",UUID.randomUUID().toString());
        ObjectNode p=event.putObject("payload");
        for(String field:new String[]{"issueUid","sessionUid","originalCommandUid","archiveEvidenceSha256"})p.set(field,h.path(field));
        p.put("businessValue","NONE").put("evidenceKind",kind).put("evidenceIndex",index).put("evidenceSha256",hash(bytes))
            .put("evidenceSizeBytes",bytes.length).put("partCount",(bytes.length+255)/256).put("partIndex",part)
            .put("dataHex",HexFormat.of().formatHex(bytes,(part-1)*256,Math.min(part*256,bytes.length)));
        var c=new DeviceConfigurationCanonicalizer();event.put("payloadSha256",c.hex(c.payloadSha256(mapper.convertValue(p,Map.class))));return event;
    }

    @Test void delayedArchiveAndItsRetryNeverDeleteALaterOccupancy(){
        jdbc.update("UPDATE dev_device_occupancy SET delivery_session_id=31");
        assertTrue(apply().changed()); assertFalse(apply().changed());
        assertEquals(31L,jdbc.queryForObject("SELECT delivery_session_id FROM dev_device_occupancy",Long.class));
        assertEquals(1,count("p1bk_effect"));
    }

    @ParameterizedTest @ValueSource(strings={"physical","order","finished","aborted"})
    void previouslyCompletedOrConflictingBusinessCannotBeRewritten(String kind){
        switch(kind){
            case "physical" -> jdbc.update("INSERT INTO dev_physical_result VALUES(40)");
            case "order" -> jdbc.update("INSERT INTO rec_delivery_order VALUES(30)");
            case "finished" -> jdbc.update("UPDATE dev_delivery_session SET status='BUSINESS_CONFIRMED',ended_at=NOW(3)");
            case "aborted" -> jdbc.update("UPDATE dev_delivery_session SET status='DEVICE_ABORTED',ended_at=NOW(3),end_reason='OTHER_REASON'");
        }
        assertThrows(UntrustedInboxSourceException.class,this::apply);
        assertEquals(0,count("dev_delivery_issue")); assertEquals(0,count("p1bk_effect"));
        assertEquals(1,count("dev_device_occupancy"));
    }

    @ParameterizedTest @ValueSource(strings={"device","session","command","config","port","cloudDigest","cloudContent","sameBoot","reason","value","missingProof"})
    void incorrectOriginalIdentityOrRebootEvidenceHasNoEffects(String field){
        ObjectNode p=(ObjectNode)normalized.path("event").path("payload");
        switch(field){
            case "device" -> ((ObjectNode)normalized.path("trustedSource")).put("deviceName","OTHER-DEVICE");
            case "session" -> jdbc.update("UPDATE dev_delivery_session SET session_uid=?",UUID.randomUUID().toString());
            case "command" -> jdbc.update("UPDATE dev_device_command SET command_uid=?",UUID.randomUUID().toString());
            case "config" -> jdbc.update("UPDATE dev_delivery_session SET device_config_version_no=9");
            case "port" -> jdbc.update("UPDATE dev_port SET port_no=3");
            case "cloudDigest" -> jdbc.update("UPDATE dev_device_command SET semantic_payload_sha256=?",new byte[32]);
            case "cloudContent" -> jdbc.update("UPDATE dev_device_command SET semantic_payload=JSON_SET(semantic_payload,'$.deliveryAutoCloseMs',50000)");
            case "sameBoot" -> p.put("targetMcuBootId",101);
            case "reason" -> p.put("reason","WEIGHT_ALL_LOST");
            case "value" -> p.put("businessValue","NORMAL");
            case "missingProof" -> p.put("bootObservationPayloadHex","");
        }
        resign(); assertThrows(UntrustedInboxSourceException.class,this::apply);
        assertEquals(0,count("dev_delivery_issue")); assertEquals(0,count("p1bk_effect"));
        assertEquals(1,count("dev_device_occupancy"));
    }
    @SuppressWarnings("unchecked") private void resign(){
        var c=new DeviceConfigurationCanonicalizer();
        ((ObjectNode)normalized.path("event")).put("payloadSha256",c.hex(c.payloadSha256(mapper.convertValue(normalized.path("event").path("payload"),Map.class))));
    }
}
