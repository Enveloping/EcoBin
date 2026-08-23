#!/usr/bin/env python3
"""Validate independently signed build receipts and rootfs qualification evidence."""
from __future__ import annotations
import hashlib, json, os, pathlib, re, subprocess
from typing import Any

class ProvenanceError(RuntimeError): pass
HEX=re.compile(r"^[0-9a-f]{64}$")
UID=re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TIME=re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

def digest(path:pathlib.Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(1024*1024): h.update(b)
    return h.hexdigest()

def regular(path:pathlib.Path,label:str,limit:int)->bytes:
    st=path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_nlink!=1 or st.st_size>limit: raise ProvenanceError(f"{label} must be a bounded regular single-link file")
    return path.read_bytes()

def load(payload:bytes,label:str)->dict[str,Any]:
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise ProvenanceError(f"duplicate key in {label}")
            out[k]=v
        return out
    value=json.loads(payload,object_pairs_hook=pairs)
    if not isinstance(value,dict): raise ProvenanceError(f"{label} must be an object")
    return value

def fingerprint(public:pathlib.Path)->str:
    payload=regular(public,"builder receipt public key",16384)
    result=subprocess.run(["openssl","pkey","-pubin","-outform","DER"],input=payload,capture_output=True)
    if result.returncode: raise ProvenanceError("builder receipt public key is invalid")
    return hashlib.sha256(result.stdout).hexdigest()

def verify_signature(payload:bytes,signature:bytes,public:pathlib.Path)->None:
    if len(signature)!=64: raise ProvenanceError("builder receipt signature must be raw Ed25519")
    # OpenSSL cannot receive both payload and signature through stdin; use private temp files.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ecobin-receipt-") as d:
        p=pathlib.Path(d); (p/"payload").write_bytes(payload); (p/"sig").write_bytes(signature)
        result=subprocess.run(["openssl","pkeyutl","-verify","-pubin","-inkey",str(public),"-rawin","-in",str(p/"payload"),"-sigfile",str(p/"sig")],capture_output=True)
    if result.returncode: raise ProvenanceError("builder receipt signature verification failed")

def validate_evidence(path:pathlib.Path,policy:dict[str,Any],layout:dict[str,Any],layout_path:pathlib.Path,builder:dict[str,Any])->dict[str,Any]:
    payload=regular(path,"rootfs qualification evidence",1024*1024)
    if hashlib.sha256(payload).hexdigest()!=policy["rootfsQualification"]["evidenceSha256"]: raise ProvenanceError("rootfs qualification evidence digest differs from policy")
    value=load(payload,"rootfs qualification evidence")
    required={"schemaVersion","artifactClass","method","imageLayoutSha256","sourceGeometry","builderDigest","e2fsprogsVersion","verification"}
    if set(value)!=required or value["schemaVersion"]!=1 or value["artifactClass"]!="DETERMINISTIC_EXT4_ROOTFS_QUALIFICATION" or value["method"]!="DETERMINISTIC_EXT4_REBUILD_V1": raise ProvenanceError("rootfs qualification evidence structure is invalid")
    facts={"differentSourceCtime":True,"semanticInventoryMatch":True,"byteIdenticalAfterNormalization":True,"exactExt4Profile":True,"readOnlyE2fsckClean":True,"fullPartitionCoverage":True}
    if value["imageLayoutSha256"]!=digest(layout_path) or value["sourceGeometry"]!=layout["sourceGeometry"] or value["builderDigest"]!=builder["container"]["digest"] or value["e2fsprogsVersion"]!=builder["tools"]["e2fsprogs"] or value["verification"]!=facts: raise ProvenanceError("rootfs qualification evidence differs from locked geometry or deterministic test facts")
    return value

def validate_receipts(items:list[tuple[pathlib.Path,pathlib.Path,pathlib.Path,pathlib.Path,pathlib.Path]],policy:dict[str,Any],builder:dict[str,Any],release_id:str,version:str,git_commit:str,locks:dict[str,str])->list[dict[str,Any]]:
    trusted={(x["builderIdentity"],x["builderDomain"]):x for x in policy["builderReceipts"]}; result=[]
    for receipt_path,sig_path,pub_path,image,manifest in items:
        payload=regular(receipt_path,"build receipt",1024*1024); sig=regular(sig_path,"build receipt signature",4096); verify_signature(payload,sig,pub_path); value=load(payload,"build receipt")
        required={"schemaVersion","artifactClass","invocationUid","builderIdentity","builderDomain","builder","releaseId","version","gitCommit","inputLocks","candidate","completedAt","signingKeyId"}
        identity=trusted.get((value.get("builderIdentity"),value.get("builderDomain")))
        expected_builder={"containerDigest":builder["container"]["digest"],"platform":builder["container"]["platform"],"sourceDateEpoch":builder["sourceDateEpoch"]}
        expected_candidate={"rawImageBytes":image.stat().st_size,"rawImageSha256":digest(image),"manifestSha256":digest(manifest)}
        if set(value)!=required or value.get("schemaVersion")!=1 or value.get("artifactClass")!="SIGNED_INDEPENDENT_IMAGE_BUILD_RECEIPT" or identity is None or fingerprint(pub_path)!=identity["publicKeySha256"] or value.get("signingKeyId")!=identity["keyId"] or not UID.fullmatch(str(value.get("invocationUid",""))) or not TIME.fullmatch(str(value.get("completedAt",""))) or value.get("builder")!=expected_builder or value.get("releaseId")!=release_id or value.get("version")!=version or value.get("gitCommit")!=git_commit or value.get("inputLocks")!=locks or value.get("candidate")!=expected_candidate: raise ProvenanceError("signed build receipt differs from controlled build facts")
        result.append(value)
    if len({x["invocationUid"] for x in result})!=2 or len({x["builderIdentity"] for x in result})!=2 or len({x["builderDomain"] for x in result})!=2: raise ProvenanceError("build receipts must have different invocation UIDs, builder identities and domains")
    return result
