"""Optional prompt-category routing to the exact official V-only generator."""
from datetime import datetime,timezone
import hashlib,json,os,re,shutil,sys,time
from pathlib import Path
from .official_entry import supervise
from .official_suite import BASELINE_SHA256
from .services import model_ready

def use_direct_bugfix(question,policy=None):
    if policy in (None,'upstream'):return False
    if policy not in {'bugfix_direct_v1','mux_bugfix_direct_v1'}:raise ValueError('Unknown task router: '+str(policy))
    if policy=='mux_bugfix_direct_v1' and not re.search(r'\b(?:mux|multiplexer)\b',question,re.I):return False
    return bool(re.search(r'\b(?:buggy|bugs?)\b',question,re.I)
                and re.search(r'\b(?:fix(?:es|ing)?|repair(?:ed|ing)?|correct(?:ed|ing)?|buggy)\b',question,re.I))

def run_direct_bugfix(task,out,serving,workflow,*,deadline_s):
    started=time.monotonic();out.mkdir(parents=True,exist_ok=True)
    if any(out.iterdir()):raise FileExistsError('Refusing to replace task artifacts: '+str(out))
    baseline=Path(os.environ['QIANSAI_OFFICIAL_BASELINE']).resolve()
    sha=hashlib.sha256(baseline.read_bytes()).hexdigest()
    if sha!=BASELINE_SHA256:raise ValueError('Official baseline identity differs')
    if not model_ready(serving['v']):raise RuntimeError('V service must already be ready')
    interface=(task/'interface.txt').read_text() if (task/'interface.txt').exists() else None
    if interface:
        clean=re.sub(r'/\*.*?\*/|//[^\n]*','',interface,flags=re.S)
        if not re.fullmatch(r'\s*module\s+\w+\s*\([^();]*\)\s*;\s*endmodule\s*',clean,re.S):
            raise ValueError('Supplied interface must be an interface-only module stub')
    (out/'config.json').write_text(json.dumps({'started_at':datetime.now(timezone.utc).isoformat(),
        'serving':serving,'workflow':workflow,'deadline_s':deadline_s,'route':workflow['task_router']},indent=2)+'\n')
    (out/'input.json').write_text(json.dumps({'task_id':task.name,'prompt':(task/'prompt.txt').read_text(),
        'interface':interface,
        'input_scope':'Same prompt and optional supplied interface as unchanged official baseline; no reference or testbench body'},indent=2)+'\n')
    env={**os.environ,'LLM_BASE_URL':serving['v']['base_url'],'MODEL_NAME':serving['v']['model'],
         'NO_PROXY':'127.0.0.1,localhost,::1','no_proxy':'127.0.0.1,localhost,::1','TRACK':'rtl'}
    for key in ['FPGACHINA_TOKEN','HF_TOKEN','HUGGING_FACE_HUB_TOKEN']:env.pop(key,None)
    raw=out/'direct_generation';budget=deadline_s-3-(time.monotonic()-started)
    if budget<=0:raise TimeoutError('No remaining direct-generation budget')
    op=supervise([sys.executable,str(baseline),str(task),str(raw),'rtl'],out/'direct_command.log',budget,env=env)
    (out/'direct_command.json').write_text(json.dumps(op,indent=2)+'\n')
    if op['returncode'] or op['timed_out']:raise RuntimeError('Direct generation failed; inspect direct_command.json')
    code=(raw/'solution.v').read_text()
    shutil.copy2(raw/'trace.jsonl',out/'trace.jsonl')
    result={'task_id':task.name,'verilog':code,'python':'','turns_used':1,'matched':False,
            'best_match_rate':None,'seconds':time.monotonic()-started,'task_router':workflow['task_router'],
            'official_baseline_sha256':sha,'P_model_calls':0,'interface_source':'official_optional_interface',
            'verification_scope':'One model-generated V candidate using unchanged official script; no self-agreement claim; independent judgement remains separate',
            'request_settings':'unchanged official temperature0/top_p1/max_tokens16384/timeout840/generation settings'}
    (out/'solution.v').write_text(code)
    (out/'generation.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
