"""A local fake native executable exercises the real Anyo subprocess boundary."""

import sys

import pytest


@pytest.fixture
def executable(tmp_path):
    script = tmp_path / "omo-fixture"
    script.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, signal, subprocess, sys, time
from pathlib import Path

def emit(value):
    print(json.dumps(value), flush=True)

if '--version' in sys.argv:
    print('omo 5.1.21 (engine: test)')
    sys.exit(0)
policy_path = Path(os.environ['ANYO_OMO_POLICY'])
policy = json.loads(policy_path.read_text())
secret = os.environ['ANYO_OMO_KEY_0']
assert secret not in ' '.join(sys.argv)
assert all(secret not in p.read_text() for p in policy_path.parent.glob('*.json'))
assert 'OPENAI_API_KEY' not in os.environ
assert 'OMO_PROFILE' not in os.environ
assert '--mode' in sys.argv and sys.argv[sys.argv.index('--mode')+1] == 'rpc'
assert '--no-model-fallback' in sys.argv
request = json.loads(input())
if (Path.cwd() / 'no-bridge').exists():
    ready = False
else:
    ready = True
    emit({'type':'anyo.ready', 'model':policy['selected']})
provider, model = policy['selected'].split('/', 1)
emit({'type':'response','id':request['id'],'command':'get_state','success':True,
      'data':{'model':{'provider':provider,'id':model},'sessionId':'session-'+str(os.getpid())}})
request = json.loads(input())
prompt = request['message']
Path('received.json').write_text(json.dumps({'message':prompt,'pid':os.getpid(),
    'model':policy['selected'],'state':str(policy_path.parent)}))
emit({'type':'response','id':request['id'],'success':True})
emit({'type':'agent_start'})
if 'HANG' in prompt:
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    Path('grandchild.pid').write_text(str(child.pid))
    time.sleep(60)
if 'WRONG_MODEL' in prompt:
    emit({'type':'anyo.model','model':'minimax/unregistered'})
elif 'INTERACTION' in prompt:
    emit({'type':'extension_ui_request','method':'confirm','id':'q'})
elif 'REJECT' in prompt:
    emit({'type':'response','success':False,'error':secret})
elif 'PROVIDER_ERROR' in prompt:
    emit({'type':'message_end','message':{'role':'assistant','stopReason':'error','errorMessage':secret}})
elif 'CRASH' in prompt:
    sys.stderr.write(secret)
    sys.exit(1)
elif 'BAD_JSON' in prompt:
    print('not JSON',flush=True)
elif 'OVERSIZED' in prompt:
    print('x'*1_100_000,flush=True)
else:
    emit({'type':'anyo.model','model':policy['selected']})
    emit({'type':'message_update','assistantMessageEvent':
          {'type':'thinking_delta','delta':'private reasoning '+secret}})
    emit({'type':'message_update','assistantMessageEvent':
          {'type':'text_delta','delta':'public '+secret}})
    emit({'type':'tool_execution_start','toolName':'read'})
    emit({'type':'tool_execution_end','toolName':'read','isError':False,'result':{}})
    if 'MISSING' not in prompt:
        result = {'type':'success','data':{'ok':True},'failure_code':None,'failure_message':None}
        if 'AGENT_FAILURE' in prompt:
            result = {'type':'failure','data':None,'failure_code':'missing_fixture',
                      'failure_message':'Cannot review'}
        if 'INVALID' in prompt:
            result['failure_code'] = 'inconsistent'
        emit({'type':'tool_execution_end','toolName':'StructuredOutput','toolCallId':'output',
              'isError':False,'result':{'details':{'anyo_result':result}}})
    emit({'type':'agent_end','willRetry':False,'messages':[]})
time.sleep(60)
"""
    )
    script.chmod(0o700)
    return script
