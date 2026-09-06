#!/usr/bin/env python3
import argparse,json,math,sys
from pathlib import Path
REQ={'docs','bug','contract','ui','integration','release','resume'}
a=argparse.ArgumentParser();a.add_argument('--route');a.add_argument('--path',action='append',default=[]);a.add_argument('--workstream');a.add_argument('--format',choices=['text','json'],default='text');a.add_argument('--root',default='.');a.add_argument('--template-mode',action='store_true');x=a.parse_args();root=Path(x.root);err=[];reports=[]
try:
 p=json.loads((root/'.engineering/documentation-policy.json').read_text());b=json.loads((root/'.engineering/baseline.json').read_text())
 if p.get('schema_version')!=2:raise ValueError('documentation policy schema_version must be 2')
 if not REQ.issubset(p.get('context_routes',{})):raise ValueError('missing context route')
 cpt=p.get('estimated_token_characters',4);profiles=set(b.get('profiles',[]));names=[x.route] if x.route else list(p['context_routes'])
 for n in names:
  rr=p['context_routes'][n];req=rr.get('requires_profile')
  if req and req not in profiles and not x.template_mode:continue
  files=list(rr['files']);
  if x.workstream and rr.get('include_workstream'):files.append(x.workstream)
  total=0
  for f in files:
   q=root/f
   if not q.is_file():raise ValueError('missing context source: '+f)
   total+=math.ceil(len(q.read_text())/cpt)
  if total>rr['max_estimated_tokens']:err.append(f'route {n} ~{total} exceeds {rr["max_estimated_tokens"]}')
  reports.append({'route':n,'estimated_tokens':total,'budget':rr['max_estimated_tokens']})
 boot=math.ceil(len((root/'AGENTS.md').read_text())/cpt);limit=p['context_targets']['bootstrap_max_estimated_tokens']
 if boot>limit:err.append(f'bootstrap ~{boot} exceeds {limit}')
 out={'bootstrap_estimated_tokens':boot,'routes':reports,'errors':err,'result':'FAIL' if err else 'PASS'}
except Exception as ex:out={'errors':[str(ex)],'result':'FAIL'}
print(json.dumps(out,indent=2) if x.format=='json' else '\n'.join(['Agent context health']+[f"{z['route']}: ~{z['estimated_tokens']} / {z['budget']}" for z in out.get('routes',[])]+['FAIL: '+z for z in out.get('errors',[])]+['RESULT: '+out['result']]))
sys.exit(out['result']!='PASS')
