"""One command for the deployed agent, unchanged baseline and official judgement."""
import argparse
from datetime import datetime
import hashlib,json,os
from pathlib import Path
import shutil,subprocess,sys,uuid
from zoneinfo import ZoneInfo
from .official_entry import terminate_tree

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True)
    p.add_argument('--official',type=Path,required=True)
    p.add_argument('--out-base',type=Path,required=True)
    p.add_argument('--tasks',type=Path)
    p.add_argument('--case',action='append')
    p.add_argument('--samples',type=int,default=1)
    a=p.parse_args()
    if a.samples<1:p.error('samples must be positive')
    package=a.package.resolve();official=a.official.resolve();base=a.out_base.resolve()
    tasks=(a.tasks or official/'tasks').resolve()
    name=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    if a.case:
        chosen=base/'task_sets'/name;chosen.mkdir(parents=True,exist_ok=False)
        for case in dict.fromkeys(a.case):
            if Path(case).name!=case or case in {'.','..'}:p.error('case must be a task directory name')
            source=tasks/case
            if not (source/'task.json').is_file():p.error('Unknown task: '+case)
            shutil.copytree(source,chosen/case)
        tasks=chosen
    selected=sorted(p.parent.name for p in tasks.glob('*/task.json'))
    if not selected:p.error('No tasks found')
    run=base/'runs'/name;run.parent.mkdir(parents=True,exist_ok=True)
    records=base/'launches';records.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'SERVE_LOG_DIR':str(base/'resident_service_logs')}
    env.pop('FPGACHINA_TOKEN',None)
    subprocess.run([str(package/'serve/start.sh')],env=env,check=True)
    argv=[sys.executable,'-m','qiansai.official_suite','--official',str(official),
          '--tasks',str(tasks),'--run-dir',str(run),'--model-root',env['MODEL_ROOT'],
          '--serving',str(package/'configs/serving.yaml'),'--config',str(package/'configs/workflow.yaml'),
          '--samples',str(a.samples),'--deadline-s','900','--judge-timeout','300','--no-start']
    record={'started_at':datetime.now().astimezone().isoformat(),'argv':argv,'selected':selected,
            'package_manifest_sha256':hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
            'scope':'Official independent development selftest; raw levels retained; cost/engineering score unmeasured'}
    (records/(name+'.json')).write_text(json.dumps(record,indent=2)+'\n')
    print('Official tasks: '+', '.join(selected)+'; samples per mode: '+str(a.samples),flush=True)
    print('Results: '+str(run),flush=True)
    with (records/(name+'.log')).open('x') as stream:
        process=subprocess.Popen(argv,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=process.wait()
        except BaseException:
            terminate_tree(process)
            raise
    for path in sorted((run/'selftest/results').glob('*.json')):
        d=json.loads(path.read_text());mode,task,sample=path.stem.split('.')
        print(f"{task} {mode} {sample}: L{d['level']} mismatches={d.get('mismatches')} samples={d.get('samples')} tool_error={d.get('tool_error')}",flush=True)
    score=run/'selftest/score.json'
    if score.exists():
        d=json.loads(score.read_text())
        print(json.dumps({'capability_score':d.get('capability_score'),'gain_score':d.get('gain_score'),
                          'set_scores':{k:v['set_score'] for k,v in d['modes'].items()},
                          'samples_per_task':a.samples,'scope':'selected public development tasks only'}),flush=True)
    print('Raw evidence: '+str(run),flush=True)
    raise SystemExit(code)

if __name__=='__main__':main()
