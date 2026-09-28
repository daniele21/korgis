#!/usr/bin/env python3
import json,sys
from pathlib import Path
r=Path('.');e=[]
for p in ['AGENTS.md','.engineering/baseline.json','.engineering/commands.json','.engineering/e2e.json','.engineering/documentation-policy.json','scripts/verify_operations.py','scripts/verify_agent_context.py']:
 if not (r/p).is_file():e.append('missing '+p)
try:b=json.loads((r/'.engineering/baseline.json').read_text())
except Exception as x:e.append(str(x));b={}
if b.get('standard',{}).get('source')!='daniele21/repo-template-sw':e.append('baseline source mismatch')
if b.get('standard',{}).get('version')!='0.10.0':e.append('baseline version must be 0.10.0')
print('Repository baseline check');[print('FAIL:',x) for x in e];print('RESULT:','FAIL' if e else 'PASS');sys.exit(bool(e))
