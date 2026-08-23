from __future__ import annotations
import hashlib, json, pathlib, subprocess, sys, tempfile, unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"lib"))
from build_provenance import ProvenanceError, digest, validate_evidence, validate_receipts

class BuildProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.base=pathlib.Path(self.tmp.name)
        self.builder={"container":{"digest":"sha256:"+"a"*64,"platform":"linux/arm64"},"sourceDateEpoch":1700000000,"tools":{"e2fsprogs":"1.47.0-2+b2"}}
        self.layout={"sourceGeometry":{"lockState":"LOCKED","rawImageSha256":"b"*64,"bootPrefixSha256":"c"*64}}
        self.layout_path=self.base/"layout.json"; self.write(self.layout_path,self.layout)
        self.image_a=self.base/"a.img"; self.image_a.write_bytes(b"same-image")
        self.image_b=self.base/"b.img"; self.image_b.write_bytes(self.image_a.read_bytes())
        self.manifest_a=self.base/"a.json"; self.manifest_b=self.base/"b.json"
        self.manifest_a.write_bytes(b"same-manifest"); self.manifest_b.write_bytes(self.manifest_a.read_bytes())
        self.locks={"sourceLockSha256":"1"*64,"builderLockSha256":"2"*64,"aptPackagesLockSha256":"3"*64,"imageLayoutSha256":"4"*64,"softwarePayloadLockSha256":"5"*64}
        self.policy={"rootfsQualification":{"evidenceSha256":""},"builderReceipts":[]}
        self.keys=[]
        for index in (0,1):
            private=self.base/f"k{index}.private"; public=self.base/f"k{index}.public"
            subprocess.run(["openssl","genpkey","-algorithm","ED25519","-out",str(private)],check=True,capture_output=True)
            subprocess.run(["openssl","pkey","-in",str(private),"-pubout","-out",str(public)],check=True,capture_output=True)
            der=subprocess.run(["openssl","pkey","-pubin","-in",str(public),"-outform","DER"],check=True,capture_output=True).stdout
            self.keys.append((private,public)); self.policy["builderReceipts"].append({"builderIdentity":f"builder-{index}","builderDomain":f"domain-{index}","keyId":f"receipt-{index}","publicKeySha256":hashlib.sha256(der).hexdigest()})

    @staticmethod
    def write(path,value): path.write_text(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
    def receipt(self,index,uid,image,manifest):
        value={"schemaVersion":1,"artifactClass":"SIGNED_INDEPENDENT_IMAGE_BUILD_RECEIPT","invocationUid":uid,"builderIdentity":f"builder-{index}","builderDomain":f"domain-{index}","builder":{"containerDigest":self.builder["container"]["digest"],"platform":"linux/arm64","sourceDateEpoch":1700000000},"releaseId":"r1","version":"1.0.0","gitCommit":"a"*40,"inputLocks":self.locks,"candidate":{"rawImageBytes":image.stat().st_size,"rawImageSha256":digest(image),"manifestSha256":digest(manifest)},"completedAt":f"2026-08-2{index+1}T12:00:00Z","signingKeyId":f"receipt-{index}"}
        path=self.base/f"receipt-{index}.json"; sig=self.base/f"receipt-{index}.sig"; self.write(path,value)
        subprocess.run(["openssl","pkeyutl","-sign","-inkey",str(self.keys[index][0]),"-rawin","-in",str(path),"-out",str(sig)],check=True,capture_output=True)
        return path,sig,self.keys[index][1]
    def test_two_distinct_signed_receipts_pass_but_repeat_or_forgery_fails(self):
        one=self.receipt(0,"11111111-1111-4111-8111-111111111111",self.image_a,self.manifest_a)
        two=self.receipt(1,"22222222-2222-4222-8222-222222222222",self.image_b,self.manifest_b)
        items=[(*one,self.image_a,self.manifest_a),(*two,self.image_b,self.manifest_b)]
        self.assertEqual(len(validate_receipts(items,self.policy,self.builder,"r1","1.0.0","a"*40,self.locks)),2)
        with self.assertRaises(ProvenanceError): validate_receipts([items[0],items[0]],self.policy,self.builder,"r1","1.0.0","a"*40,self.locks)
        two[1].write_bytes(b"x"*64)
        with self.assertRaisesRegex(ProvenanceError,"signature verification failed"): validate_receipts(items,self.policy,self.builder,"r1","1.0.0","a"*40,self.locks)
    def test_evidence_missing_or_wrong_digest_is_rejected(self):
        evidence=self.base/"evidence.json"
        facts={"differentSourceCtime":True,"semanticInventoryMatch":True,"byteIdenticalAfterNormalization":True,"exactExt4Profile":True,"readOnlyE2fsckClean":True,"fullPartitionCoverage":True}
        self.write(evidence,{"schemaVersion":1,"artifactClass":"DETERMINISTIC_EXT4_ROOTFS_QUALIFICATION","method":"DETERMINISTIC_EXT4_REBUILD_V1","imageLayoutSha256":digest(self.layout_path),"sourceGeometry":self.layout["sourceGeometry"],"builderDigest":self.builder["container"]["digest"],"e2fsprogsVersion":self.builder["tools"]["e2fsprogs"],"verification":facts})
        self.policy["rootfsQualification"]["evidenceSha256"]=digest(evidence)
        validate_evidence(evidence,self.policy,self.layout,self.layout_path,self.builder)
        self.policy["rootfsQualification"]["evidenceSha256"]="0"*64
        with self.assertRaisesRegex(ProvenanceError,"digest differs"): validate_evidence(evidence,self.policy,self.layout,self.layout_path,self.builder)
        with self.assertRaises(OSError): validate_evidence(self.base/"missing",self.policy,self.layout,self.layout_path,self.builder)

if __name__=="__main__": unittest.main()
