package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.result.NativeDeliveryIssueEvidence;
import org.enveloping.ecobin.device.api.uart.EcobinUartProtocol;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.io.ByteArrayOutputStream;
import java.sql.ResultSet;
import java.security.NoSuchAlgorithmException;

/** Freezes one original delivery as diagnostic-only; deliberately has no recycling/funds/bag writer. */
@Service
public class NativeDeliveryIssueService {
    public static final String ARCHIVE_EVENT = "DELIVERY_ISSUE_ARCHIVED";
    public static final String EVIDENCE_EVENT = "DELIVERY_ISSUE_EVIDENCE_APPENDED";
    private static final String REASON = "MCU_RESTART_FINAL_RESULT_UNAVAILABLE";
    private static final Set<String> ACTIVE = Set.of("PREPARED","AUTHORIZATION_QUEUED","IN_PROGRESS","RESULT_PENDING_RECOVERY");
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final ReliableDeviceTaskProofPort proof;

    public NativeDeliveryIssueService(JdbcTemplate jdbc, ObjectMapper mapper,
            DeviceConfigurationCanonicalizer canonicalizer, ReliableDeviceTaskProofPort proof) {
        this.jdbc=jdbc; this.mapper=mapper; this.canonicalizer=canonicalizer; this.proof=proof;
    }

    @Transactional(propagation=Propagation.MANDATORY)
    public ApplyResult applyTrusted(long sourceInboxId, JsonNode normalized, LocalDateTime now) {
        JsonNode event=normalized.path("event"), payload=event.path("payload");
        if(EVIDENCE_EVENT.equals(event.path("eventType").asText())) return appendEvidence(sourceInboxId,normalized,now);
        String device=text(normalized.path("trustedSource"),"deviceName");
        exact(event,"eventType",ARCHIVE_EVENT);
        exact(payload,"businessValue","NONE"); exact(payload,"reason",REASON); exact(payload,"finalResultAtArchive","ABSENT");
        String issue=uid(payload,"issueUid"), session=uid(payload,"sessionUid"), command=uid(payload,"originalCommandUid");
        exact(event,"eventUid",issue); exact(event,"commandUid",command);
        exact(event.path("target"),"type","DELIVERY_SESSION"); exact(event.path("target"),"uid",session);
        String payloadSha=sha(event,"payloadSha256"), archiveSha=sha(payload,"archiveEvidenceSha256");
        if (!payloadSha.equals(digest(payload)) || sourceInboxId<1 || now==null) throw untrusted();
        long sourceBoot=integer(payload,"sourceMcuBootId"),targetBoot=integer(payload,"targetMcuBootId");
        long port=integer(payload,"portNo");
        if(port<1||port>6) throw untrusted();
        NativeDeliveryIssueEvidence.Start start;
        try {
            start=NativeDeliveryIssueEvidence.validateArchive(text(payload,"originalStartPayloadHex"),
                    text(payload,"bootObservationType"),text(payload,"bootObservationPayloadHex"),
                    UUID.fromString(session),(int)port,sourceBoot,targetBoot);
        } catch (IllegalArgumentException invalidEvidence) {
            throw untrusted();
        }
        long count=integer(payload,"knownFactCount");
        if(count<0||count>0xffff_ffffL) throw untrusted();
        var assets=jdbc.query("SELECT id FROM dev_device_asset WHERE hardware_sn=? FOR UPDATE",(rs,n)->rs.getLong(1),device);
        if(assets.size()!=1) throw untrusted();
        long asset=assets.getFirst();
        var existing=jdbc.query("SELECT asset_id,event_payload_sha256 FROM dev_delivery_issue WHERE issue_uid=?",
                (rs,n)->new Existing(rs.getLong(1),rs.getBytes(2)),issue);
        if(!existing.isEmpty()) {
            if(existing.size()!=1||existing.getFirst().asset()!=asset
                    || !MessageDigest.isEqual(existing.getFirst().sha(),HexFormat.of().parseHex(payloadSha))) throw untrusted();
            return new ApplyResult(asset,false);
        }
        // dev_port is immutable to the runtime principal. Read its scoped identity without
        // including it in the outer locking join; only the original session/command can change.
        var rows=jdbc.query("""
                SELECT s.id,s.tenant_id,s.organization_id,s.status,s.ended_at,s.device_completed_at,
                       s.device_config_version_no,s.device_config_content_sha256,c.id AS command_id,
                       c.semantic_payload,c.semantic_payload_sha256,
                       (SELECT p.port_no FROM dev_port p WHERE p.id=s.port_id AND p.asset_id=s.asset_id
                           AND p.tenant_id=s.tenant_id AND p.organization_id=s.organization_id) AS port_no
                FROM dev_delivery_session s
                JOIN dev_device_command c ON c.delivery_session_id=s.id AND c.asset_id=s.asset_id AND c.tenant_id=s.tenant_id AND c.organization_id=s.organization_id
                WHERE s.asset_id=? AND s.session_uid=? AND c.command_uid=? AND c.command_type='START_DELIVERY_SESSION'
                FOR UPDATE
                """,(rs,n)->new Original(rs.getLong("id"),rs.getLong("tenant_id"),rs.getLong("organization_id"),
                rs.getString("status"),rs.getObject("ended_at"),rs.getObject("device_completed_at"),
                rs.getLong("device_config_version_no"),rs.getBytes("device_config_content_sha256"),rs.getLong("command_id"),
                rs.getString("semantic_payload"),rs.getBytes("semantic_payload_sha256"),rs.getInt("port_no")),asset,session,command);
        if(rows.size()!=1) throw untrusted();
        Original original=rows.getFirst();
        if(!ACTIVE.contains(original.status())||original.ended()!=null||original.completed()!=null
                ||original.port()!=port||original.config()!=start.configVersion()
                ||!MessageDigest.isEqual(original.configSha(),HexFormat.of().parseHex(start.configContentSha256()))
                ||!MessageDigest.isEqual(original.commandSha(),HexFormat.of().parseHex(sha(payload,"originalCommandPayloadSha256")))) throw untrusted();
        JsonNode cloud=mapper.readTree(original.commandJson());
        if(!digest(cloud).equals(sha(payload,"originalCommandPayloadSha256"))) throw untrusted();
        exact(cloud,"sessionUid",session);
        if(integer(cloud,"portNo")!=port||integer(cloud.path("config"),"version")!=start.configVersion()) throw untrusted();
        exact(cloud.path("config"),"contentSha256",start.configContentSha256());
        ByteBuffer raw=ByteBuffer.wrap(HexFormat.of().parseHex(text(payload,"originalStartPayloadHex")));
        raw.position(EcobinUartProtocol.START_DELIVERY_SESSION_UNIT_PRICE_TEN_THOUSANDTHS_OFFSET);
        if(integer(cloud,"unitPriceTenThousandths")!=Integer.toUnsignedLong(raw.getInt())
                ||integer(cloud,"continueDeliveryWaitMs")!=Integer.toUnsignedLong(raw.getInt())
                ||integer(cloud,"negativeWeightThresholdGrams")!=Integer.toUnsignedLong(raw.getInt())) throw untrusted();
        raw.getInt(); // Pi computes START acceptance window from original authorization, not a cloud payload field.
        if(integer(cloud,"deliveryAutoCloseMs")!=Integer.toUnsignedLong(raw.getInt())) throw untrusted();
        int results=jdbc.queryForObject("SELECT (SELECT COUNT(*) FROM dev_physical_result WHERE command_id=?) + (SELECT COUNT(*) FROM rec_delivery_order WHERE delivery_session_id=?)",
                Integer.class,original.commandId(),original.id());
        if(results!=0) throw untrusted();
        one(jdbc.update("""
                INSERT INTO dev_delivery_issue(issue_uid,tenant_id,organization_id,asset_id,delivery_session_id,
                    original_command_id,source_inbox_id,event_payload_sha256,archive_evidence_sha256,
                    source_mcu_boot_id,target_mcu_boot_id,reason,business_value,header_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'NONE',?,?)
                """,issue,original.tenant(),original.organization(),asset,original.id(),original.commandId(),sourceInboxId,
                HexFormat.of().parseHex(payloadSha),HexFormat.of().parseHex(archiveSha),sourceBoot,targetBoot,REASON,mapper.writeValueAsString(payload),now));
        one(jdbc.update("""
                UPDATE dev_delivery_session SET status='DEVICE_ABORTED',ended_at=?,end_reason=?,lock_version=lock_version+1,updated_at=?
                WHERE id=? AND tenant_id=? AND organization_id=? AND asset_id=? AND ended_at IS NULL AND device_completed_at IS NULL
                    AND status IN ('PREPARED','AUTHORIZATION_QUEUED','IN_PROGRESS','RESULT_PENDING_RECOVERY')
                """,now,REASON,now,original.id(),original.tenant(),original.organization(),asset));
        // Never touch a new occupancy, a new bag/tare, the actual actuator state or later work.
        jdbc.update("DELETE FROM dev_device_occupancy WHERE asset_id=? AND tenant_id=? AND organization_id=? AND occupancy_kind='DELIVERY' AND delivery_session_id=?",
                asset,original.tenant(),original.organization(),original.id());
        proof.completeFromTrustedProof("START_DELIVERY_SESSION","DELIVERY_SESSION",session);
        return new ApplyResult(asset,true);
    }

    private ApplyResult appendEvidence(long inbox,JsonNode normalized,LocalDateTime now) {
        JsonNode event=normalized.path("event"),p=event.path("payload");
        String issue=uid(p,"issueUid"),session=uid(p,"sessionUid"),command=uid(p,"originalCommandUid");
        String eventUid=uid(event,"eventUid"),payloadSha=sha(event,"payloadSha256"),archiveSha=sha(p,"archiveEvidenceSha256");
        exact(event,"commandUid",command); exact(event.path("target"),"type","DELIVERY_SESSION"); exact(event.path("target"),"uid",session);
        exact(p,"businessValue","NONE");
        if(eventUid.equals(issue)||!payloadSha.equals(digest(p))||inbox<1||now==null)throw untrusted();
        String kind=text(p,"evidenceKind"),evidenceSha=sha(p,"evidenceSha256");
        long index=integer(p,"evidenceIndex"),size=integer(p,"evidenceSizeBytes"),parts=integer(p,"partCount"),part=integer(p,"partIndex");
        String data=text(p,"dataHex");
        if(!Set.of("ARCHIVE_CONTEXT","PROCESS_FACT","FINAL_RESULT").contains(kind)
                ||index<0||index>0xffff_ffffL||("PROCESS_FACT".equals(kind)!=(index>0))
                ||size<1||size>0xffff_ffffL||parts!=(size+255)/256||part<1||part>parts
                ||data.length()>512||!data.matches("(?:[0-9a-f]{2})+"))throw untrusted();
        byte[] bytes=HexFormat.of().parseHex(data);
        if(bytes.length!=Math.min(256,size-(part-1)*256)
                ||("ARCHIVE_CONTEXT".equals(kind)&&!archiveSha.equals(evidenceSha)))throw untrusted();
        var assets=jdbc.query("SELECT id FROM dev_device_asset WHERE hardware_sn=? FOR UPDATE",(rs,n)->rs.getLong(1),text(normalized.path("trustedSource"),"deviceName"));
        if(assets.size()!=1)throw untrusted(); long asset=assets.getFirst();
        var parents=jdbc.query("SELECT id,asset_id,header_json FROM dev_delivery_issue WHERE issue_uid=?",
                (rs,n)->new Issue(rs.getLong(1),rs.getLong(2),rs.getString(3)),issue);
        // A fragment may overtake its header in the reliable channel; retain/retry the inbox, not quarantine it as foreign.
        if(parents.isEmpty())throw new IllegalStateException("original delivery issue archive has not arrived yet");
        Issue parent=parents.getFirst(); JsonNode header=mapper.readTree(parent.header());
        if(parent.asset()!=asset)throw untrusted();
        exact(header,"sessionUid",session); exact(header,"originalCommandUid",command); exact(header,"archiveEvidenceSha256",archiveSha);
        if("PROCESS_FACT".equals(kind)&&index>integer(header,"knownFactCount"))throw untrusted();
        var metadata=jdbc.query("""
                SELECT evidence_sha256,evidence_size_bytes,part_count,part_index,part_bytes
                FROM dev_delivery_issue_part WHERE issue_id=? AND evidence_kind=? AND evidence_index=?
                ORDER BY part_index LIMIT 1
                """,(rs,n)->new Part(rs.getBytes(1),rs.getLong(2),rs.getLong(3),rs.getLong(4),rs.getBytes(5)),parent.id(),kind,index);
        for(Part row:metadata) {
            if(!MessageDigest.isEqual(row.sha(),HexFormat.of().parseHex(evidenceSha))||row.size()!=size||row.count()!=parts)throw untrusted();
        }
        var existingPart=jdbc.query("SELECT part_bytes FROM dev_delivery_issue_part WHERE issue_id=? AND evidence_kind=? AND evidence_index=? AND part_index=?",
                (rs,n)->rs.getBytes(1),parent.id(),kind,index,part);
        if(!existingPart.isEmpty()) {
            if(!MessageDigest.isEqual(existingPart.getFirst(),bytes))throw untrusted();
            return new ApplyResult(asset,false);
        }
        if(jdbc.queryForObject("SELECT COUNT(*) FROM dev_delivery_issue_part WHERE event_uid=? OR source_inbox_id=?",Integer.class,eventUid,inbox)!=0)throw untrusted();
        one(jdbc.update("""
                INSERT INTO dev_delivery_issue_part(issue_id,evidence_kind,evidence_index,evidence_sha256,
                    evidence_size_bytes,part_count,part_index,part_bytes,event_uid,event_payload_sha256,source_inbox_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,parent.id(),kind,index,HexFormat.of().parseHex(evidenceSha),size,parts,part,bytes,eventUid,HexFormat.of().parseHex(payloadSha),inbox,now));
        if(jdbc.queryForObject("SELECT COUNT(*) FROM dev_delivery_issue_part WHERE issue_id=? AND evidence_kind=? AND evidence_index=?",
                Long.class,parent.id(),kind,index)==parts) {
            MessageDigest hash;
            try{hash=MessageDigest.getInstance("SHA-256");}catch(NoSuchAlgorithmException impossible){throw new IllegalStateException(impossible);}
            ByteArrayOutputStream result=new ByteArrayOutputStream();
            if("FINAL_RESULT".equals(kind)&&size!=EcobinUartProtocol.WORK_RESULT_PAYLOAD_MIN_LENGTH)throw untrusted();
            long[] seen={0,0};
            jdbc.query(connection->{
                var ps=connection.prepareStatement("SELECT part_index,part_bytes FROM dev_delivery_issue_part WHERE issue_id=? AND evidence_kind=? AND evidence_index=? ORDER BY part_index",
                        ResultSet.TYPE_FORWARD_ONLY,ResultSet.CONCUR_READ_ONLY);
                ps.setLong(1,parent.id());ps.setString(2,kind);ps.setLong(3,index);ps.setFetchSize(Integer.MIN_VALUE);return ps;
            },(org.springframework.jdbc.core.RowCallbackHandler)rs->{
                if(rs.getLong(1)!=++seen[0])throw untrusted();
                byte[] raw=rs.getBytes(2);seen[1]+=raw.length;hash.update(raw);
                if("FINAL_RESULT".equals(kind))result.writeBytes(raw);
            });
            if(seen[0]!=parts||seen[1]!=size||!MessageDigest.isEqual(hash.digest(),HexFormat.of().parseHex(evidenceSha)))throw untrusted();
            if("FINAL_RESULT".equals(kind)) {
                try {
                    var start=NativeDeliveryIssueEvidence.validateArchive(text(header,"originalStartPayloadHex"),text(header,"bootObservationType"),
                            text(header,"bootObservationPayloadHex"),UUID.fromString(session),(int)integer(header,"portNo"),
                            integer(header,"sourceMcuBootId"),integer(header,"targetMcuBootId"));
                    NativeDeliveryIssueEvidence.validateLateResult(result.toByteArray(),start);
                }catch(IllegalArgumentException invalid){throw untrusted();}
            }
            one(jdbc.update("""
                    INSERT INTO dev_delivery_issue_evidence(issue_id,evidence_kind,evidence_index,evidence_sha256,evidence_size_bytes,part_count,created_at)
                    VALUES(?,?,?,?,?,?,?)
                    """,parent.id(),kind,index,HexFormat.of().parseHex(evidenceSha),size,parts,now));
        }
        return new ApplyResult(asset,true);
    }

    @SuppressWarnings("unchecked") private String digest(JsonNode node) {
        return HexFormat.of().formatHex(NativeDeliveryIssueEvidence.sha256(canonicalizer.canonicalBytes(mapper.convertValue(node,Map.class))));
    }
    private static String text(JsonNode parent,String field){JsonNode n=parent.path(field);if(!n.isTextual()||n.asText().isEmpty())throw untrusted();return n.asText();}
    private static void exact(JsonNode p,String f,String v){if(!v.equals(text(p,f)))throw untrusted();}
    private static long integer(JsonNode p,String f){JsonNode n=p.path(f);if(!n.isIntegralNumber()||!n.canConvertToLong())throw untrusted();return n.asLong();}
    private static String uid(JsonNode p,String f){String v=text(p,f);if(!v.matches("[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"))throw untrusted();return v;}
    private static String sha(JsonNode p,String f){String v=text(p,f);if(!v.matches("[0-9a-f]{64}"))throw untrusted();return v;}
    private static void one(int rows){if(rows!=1)throw new IllegalStateException("native issue transaction lost its original row");}
    private static UntrustedInboxSourceException untrusted(){return new UntrustedInboxSourceException("native delivery issue differs from original trusted work");}
    public record ApplyResult(long assetId,boolean changed){}
    private record Existing(long asset,byte[] sha){}
    private record Issue(long id,long asset,String header){}
    private record Part(byte[] sha,long size,long count,long index,byte[] bytes){}
    private record Original(long id,long tenant,long organization,String status,Object ended,Object completed,
            long config,byte[] configSha,long commandId,String commandJson,byte[] commandSha,int port){}
}
