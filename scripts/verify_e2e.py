#!/usr/bin/env python3
"""Zero-dependency validation for the E2E environment fidelity contract."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
FIDELITY=["host_or_fake","simulated_or_emulated","representative_virtual","representative_physical","target_environment"];RANK={x:i for i,x in enumerate(FIDELITY)};UI=["assertions","screenshots","full_media"];TRIGGERS={"material_ui_integration_outcome","motion_or_animation","timing_or_progression","navigation_or_transition_sequence","lifecycle_visibility","release_acceptance"};PRINCIPLES=("final_environment_should_confirm_not_discover","execution_capability_separate_from_environment_fidelity","lowest_sufficient_test_level","critical_journeys_only","built_artifact_when_material","residual_fidelity_gaps_explicit","ui_evidence_risk_based");MARKERS=("<REPLACE_WITH_","<PROJECT_")
def text(v):return isinstance(v,str) and bool(v.strip())
def strings(v):return isinstance(v,list) and all(text(x) for x in v)
def placeholder(v):
 if isinstance(v,str):return any(m in v for m in MARKERS)
 if isinstance(v,list):return any(placeholder(x) for x in v)
 if isinstance(v,dict):return any(placeholder(x) for x in v.values())
 return False
def keyed(items,label,e):
 out={}
 if not isinstance(items,list):e.append(f"{label} must be a list");return out
 for i,x in enumerate(items):
  if not isinstance(x,dict) or not text(x.get("id")):e.append(f"{label}[{i}].id is required");continue
  if x["id"] in out:e.append(f"duplicate {label} id: {x['id']}")
  out[x["id"]]=x
 return out
def refs(v,known,label,e,allow=False):
 if not isinstance(v,list) or not all(text(x) for x in v):e.append(f"{label} must be a list of non-empty ids");return []
 if not v and not allow:e.append(f"{label} must not be empty")
 for x in v:
  if x not in known and not placeholder(x):e.append(f"{label} references unknown id: {x}")
 return v
def main():
 p=argparse.ArgumentParser();p.add_argument("--root",default=".");p.add_argument("--template-mode",action="store_true");a=p.parse_args();root=Path(a.root).resolve();e=[];w=[]
 try:d=json.loads((root/".engineering/e2e.json").read_text());c=json.loads((root/".engineering/commands.json").read_text())
 except Exception as x:print(f"FAIL: invalid engineering JSON: {x}");return 1
 if d.get("schema_version")!=1:e.append("schema_version must be 1")
 if d.get("contract_version")!="0.2.1":e.append("contract_version must be 0.2.1")
 app=d.get("applicability") if isinstance(d.get("applicability"),dict) else {};status=app.get("status")
 if status not in {"required","recommended","n/a"}:e.append("applicability.status invalid")
 if not text(app.get("reason")):e.append("applicability.reason is required")
 cs=((c.get("commands") or {}).get("e2e") or {}).get("status")
 if status=="n/a" and cs!="n/a":e.append("E2E n/a requires commands.e2e n/a")
 if status in {"required","recommended"} and cs=="n/a":e.append("E2E-applicable repository may not set commands.e2e n/a")
 if status=="required" and cs!="required":e.append("required E2E requires commands.e2e required")
 pr=d.get("principles") if isinstance(d.get("principles"),dict) else {}
 for k in PRINCIPLES:
  if pr.get(k) is not True:e.append(f"principles.{k} must be true")
 sp=d.get("stage_policy") if isinstance(d.get("stage_policy"),dict) else {};i=sp.get("integration") if isinstance(sp.get("integration"),dict) else {};r=sp.get("release") if isinstance(sp.get("release"),dict) else {}
 for k,v in {"automated_e2e_before_shared_integration":True,"real_environment_blocking":False,"real_environment_deferred_to_release":True,"material_ui_journey_minimum_evidence_mode":"full_media","incidental_ui_may_use_assertions":True}.items():
  if i.get(k)!=v:e.append(f"stage_policy.integration.{k} must be {v!r}")
 for k,v in {"full_validation_required":True,"release_critical_e2e_required":True,"required_real_environment_blocking":True}.items():
  if r.get(k)!=v:e.append(f"stage_policy.release.{k} must be {v!r}")
 ui=d.get("ui_evidence") if isinstance(d.get("ui_evidence"),dict) else {}
 if ui.get("modes")!=UI:e.append("ui_evidence.modes must be assertions, screenshots, full_media")
 if ui.get("default_mode") not in UI:e.append("ui_evidence.default_mode invalid")
 if ui.get("assertions_allowed_when_ui_incidental") is not True:e.append("ui_evidence.assertions_allowed_when_ui_incidental must be true")
 missing=TRIGGERS-set(ui.get("full_media_triggers") or [])
 if missing:e.append("ui_evidence.full_media_triggers missing: "+", ".join(sorted(missing)))
 if d.get("fidelity_order")!=FIDELITY:e.append("fidelity_order must match canonical order")
 tr=keyed(d.get("target_environments"),"target_environments",e);ex=keyed(d.get("execution_environments"),"execution_environments",e);js=keyed(d.get("critical_journeys"),"critical_journeys",e)
 if status in {"required","recommended"} and (not tr or not ex or not js):e.append("E2E-applicable repository must declare target/execution environments and critical journeys")
 for k,x in tr.items():
  if not text(x.get("platform")):e.append(f"target_environments.{k}.platform is required")
  if not text(x.get("description")):e.append(f"target_environments.{k}.description is required")
  if not strings(x.get("material_dimensions")) or not x.get("material_dimensions"):e.append(f"target_environments.{k}.material_dimensions must be non-empty")
 automated=set()
 for k,x in ex.items():
  if x.get("fidelity_class") not in RANK:e.append(f"execution_environments.{k}.fidelity_class invalid")
  if x.get("automation") not in {"automated","real_environment"}:e.append(f"execution_environments.{k}.automation invalid")
  elif x.get("automation")=="automated":automated.add(k)
  if not text(x.get("platform")):e.append(f"execution_environments.{k}.platform is required")
  if not text(x.get("artifact_surface")):e.append(f"execution_environments.{k}.artifact_surface is required")
  refs(x.get("target_environment_refs"),set(tr),f"execution_environments.{k}.target_environment_refs",e)
  if not isinstance(x.get("known_gaps"),list) or not all(text(g) for g in x.get("known_gaps")):e.append(f"execution_environments.{k}.known_gaps must be a string list")
 for k,x in js.items():
  if not text(x.get("claim")):e.append(f"critical_journeys.{k}.claim is required")
  if not isinstance(x.get("ui_surface"),bool):e.append(f"critical_journeys.{k}.ui_surface must be boolean")
  mode=x.get("minimum_ui_evidence_mode")
  if x.get("ui_surface") is True and mode not in UI:e.append(f"critical_journeys.{k}.minimum_ui_evidence_mode invalid")
  if x.get("ui_surface") is False and mode not in {None,"assertions"}:e.append(f"critical_journeys.{k} non-UI evidence must be assertions/absent")
  refs(x.get("target_environment_refs"),set(tr),f"critical_journeys.{k}.target_environment_refs",e)
  ar=refs(x.get("automated_environment_refs"),set(ex),f"critical_journeys.{k}.automated_environment_refs",e,allow=True);rr=[]
  for ref in ar:
   env=ex.get(ref)
   if env and env.get("automation")!="automated":e.append(f"critical_journeys.{k} automated ref {ref} is not automated")
   if env and env.get("automation")=="automated" and env.get("fidelity_class") in RANK:rr.append(RANK[env.get("fidelity_class")])
  minimum=x.get("minimum_automated_fidelity")
  if minimum not in RANK:e.append(f"critical_journeys.{k}.minimum_automated_fidelity invalid")
  elif ar and rr and max(rr)<RANK[minimum]:e.append(f"critical_journeys.{k} automated fidelity below {minimum}")
  confirmation=x.get("real_environment_confirmation")
  if confirmation not in {"required","conditional","not_required"}:e.append(f"critical_journeys.{k}.real_environment_confirmation invalid")
  residual=x.get("residual_gaps")
  if not isinstance(residual,list) or not all(text(g) for g in residual):e.append(f"critical_journeys.{k}.residual_gaps must be a string list")
  if not ar and not text(x.get("automation_gap_reason")):e.append(f"critical_journeys.{k} needs automated refs or automation_gap_reason")
  if ar and not any(ref in automated for ref in ar):e.append(f"critical_journeys.{k} has no valid automated execution environment")
  if confirmation=="not_required" and residual:w.append(f"critical_journeys.{k} has residual gaps but real_environment_confirmation is not_required")
 if not a.template_mode and placeholder(d):e.append("unresolved adopter placeholder in .engineering/e2e.json")
 print("E2E environment fidelity contract check");print(f"root: {root}");print(f"applicability: {status}");print(f"commands.e2e.status: {cs}");[print(f"WARN: {x}") for x in w];[print(f"FAIL: {x}") for x in e]
 if e:print(f"RESULT: FAIL ({len(e)} error(s), {len(w)} warning(s))");return 1
 print(f"RESULT: PASS ({len(w)} warning(s))");return 0
if __name__=="__main__":sys.exit(main())
