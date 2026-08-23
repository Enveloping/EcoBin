#!/usr/bin/env python3
"""Create the canonical payload signed by one external builder-receipt key."""
from __future__ import annotations
import argparse, datetime, json, pathlib, sys, uuid
from build_provenance import digest
from validate_inputs import load_json, validate_inputs

def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config-dir",type=pathlib.Path,required=True); p.add_argument("--candidate",type=pathlib.Path,required=True); p.add_argument("--manifest",type=pathlib.Path,required=True)
    p.add_argument("--builder-identity",required=True); p.add_argument("--builder-domain",required=True); p.add_argument("--signing-key-id",required=True); p.add_argument("--invocation-uid",required=True); p.add_argument("--completed-at",required=True); p.add_argument("--target-media-qualification-evidence",type=pathlib.Path,required=True); p.add_argument("--output",type=pathlib.Path,required=True)
    try:
        a=p.parse_args(); validated=validate_inputs(a.config_dir,require_locked=True,target_media_evidence_path=a.target_media_qualification_evidence); invocation=str(uuid.UUID(a.invocation_uid))
        completed=datetime.datetime.strptime(a.completed_at,"%Y-%m-%dT%H:%M:%SZ")
        if completed.tzinfo is not None or a.output.exists(): raise ValueError("receipt output/time is invalid")
        manifest=load_json(a.manifest); artifacts=manifest["artifacts"]
        if artifacts.get("rawImageBytes")!=a.candidate.stat().st_size or artifacts.get("rawImageSha256")!=digest(a.candidate): raise ValueError("candidate differs from manifest")
        value={"schemaVersion":1,"artifactClass":"SIGNED_INDEPENDENT_IMAGE_BUILD_RECEIPT","invocationUid":invocation,"builderIdentity":a.builder_identity,"builderDomain":a.builder_domain,"builder":{"containerDigest":validated.builder["container"]["digest"],"platform":validated.builder["container"]["platform"],"sourceDateEpoch":validated.builder["sourceDateEpoch"]},"releaseId":manifest["releaseId"],"version":manifest["version"],"gitCommit":manifest["gitCommit"],"inputLocks":manifest["locks"],"candidate":{"rawImageBytes":a.candidate.stat().st_size,"rawImageSha256":digest(a.candidate),"manifestSha256":digest(a.manifest)},"completedAt":a.completed_at,"signingKeyId":a.signing_key_id}
        with a.output.open("x",encoding="utf-8",newline="\n") as f: json.dump(value,f,sort_keys=True,separators=(",",":")); f.write("\n")
    except Exception as exc:
        print(f"build-receipt=FAIL: {exc}",file=sys.stderr); return 2
    print(f"build-receipt=PASS output={a.output}"); return 0
if __name__=="__main__": raise SystemExit(main())
