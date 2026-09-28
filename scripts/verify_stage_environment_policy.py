#!/usr/bin/env python3
"""Verify automated integration and release-gated real-environment semantics."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
def expect(s,k,v,e,p):
    if s.get(k)!=v:e.append(f"{p}.{k} must be {v!r}")
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",default=".");p.add_argument("--template-mode",action="store_true");a=p.parse_args();root=Path(a.root).resolve();errors=[]
    try:c=json.loads((root/".engineering/commands.json").read_text());d=json.loads((root/".engineering/e2e.json").read_text())
    except Exception as x:print(f"FAIL: invalid engineering JSON: {x}");return 1
    v=c.get("development_velocity",{});i=v.get("integration",{});r=v.get("release",{})
    expect(i,"automated_e2e_required_when_affected",True,errors,"development_velocity.integration");expect(i,"real_environment_blocking",False,errors,"development_velocity.integration");expect(i,"real_environment_deferred_to_release",True,errors,"development_velocity.integration");expect(r,"required_real_environment_blocking",True,errors,"development_velocity.release")
    p2=d.get("stage_policy",{});i=p2.get("integration",{});r=p2.get("release",{})
    expect(i,"automated_e2e_before_shared_integration",True,errors,"stage_policy.integration");expect(i,"real_environment_blocking",False,errors,"stage_policy.integration");expect(i,"real_environment_deferred_to_release",True,errors,"stage_policy.integration");expect(i,"material_ui_journey_minimum_evidence_mode","full_media",errors,"stage_policy.integration");expect(r,"required_real_environment_blocking",True,errors,"stage_policy.release")
    print("Stage environment policy check");[print("FAIL:",x) for x in errors];print("RESULT:","FAIL" if errors else "PASS");return 1 if errors else 0
if __name__=="__main__":sys.exit(main())
