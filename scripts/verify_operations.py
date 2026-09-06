#!/usr/bin/env python3
import json,sys
from pathlib import Path
d=json.loads(Path('.engineering/commands.json').read_text());e=[]
if d.get('contract_version')!='0.7.0':e.append('contract_version must be 0.7.0')
i=d.get('development_velocity',{}).get('integration',{});r=d.get('development_velocity',{}).get('release',{})
if i.get('automated_e2e_required_when_affected') is not True or i.get('real_environment_blocking') is not False or i.get('real_environment_deferred_to_release') is not True:e.append('invalid integration stage policy')
if r.get('required_real_environment_blocking') is not True:e.append('invalid release environment policy')
rep=d.get('agent_reporting',{});req={'stage','source_identity','risks','profile','required_gates','evidence','remaining_gaps','next_action'}
if rep.get('schema_version')!=1 or rep.get('format')!='summary_with_evidence_references' or not req.issubset(set(rep.get('required_summary_fields',[]))):e.append('invalid agent_reporting')
for k in ['bounded_output','full_report_on_demand','preserve_failed_pending_gates','summary_is_not_evidence_verification']:
 if rep.get(k) is not True:e.append('agent_reporting.'+k+' must be true')
print('Project operating contract check');[print('FAIL:',x) for x in e];print('RESULT:','FAIL' if e else 'PASS');sys.exit(bool(e))
