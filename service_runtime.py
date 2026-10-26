"""Own-process supervisor for local collection/viewers. Never runs analysis."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime,timezone
import base64,hashlib,json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
from live_dashboard import atomic_write,validate_tailscale_access
from topic_display import _locked_preferences
_launched={}

def timestamp():return datetime.now(timezone.utc).isoformat()
def paths(config):
    root=config.parent/(config.stem+'-runtime');root.mkdir(parents=True,exist_ok=True)
    return root,root/'state.json',root/'stop.json'
def load_config(path):
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('version')!=1 or not isinstance(config.get('services'),list) or not config['services']:raise ValueError('Invalid runtime configuration')
    for key in ('database','codexHome'):
        if not isinstance(config.get(key),str) or not Path(config[key]).is_absolute():raise ValueError('Absolute paths required')
    if config.get('claudeHome') and not Path(config['claudeHome']).is_absolute():raise ValueError('Absolute Claude home required')
    ports=set();names=set();directories=set();watchers=0
    for service in config['services']:
        if service.get('mode') not in ('watch','serve') or not isinstance(service.get('name'),str) or not service['name'].replace('-','').isalnum():raise ValueError('Invalid service')
        if type(service.get('port')) is not int or not 1<=service['port']<=65535 or service['port'] in ports:raise ValueError('Invalid or repeated port')
        if service['name'] in names or not Path(service.get('directory','')).is_absolute() or service['directory'] in directories:raise ValueError('Invalid service directory')
        validate_tailscale_access(service.get('tailscaleOrigin'),service.get('tailscaleUser'))
        if service['mode']=='watch':
            watchers+=1
            if service.get('tailscaleOrigin'):raise ValueError('Collector must remain local')
        ports.add(service['port']);names.add(service['name']);directories.add(service['directory'])
    if watchers!=1:raise ValueError('Exactly one collector is required')
    if not isinstance(config.get('interval',10),(int,float)) or not 1<=config.get('interval',10)<=3600:raise ValueError('Invalid interval')
    return config
def connected(port):
    with socket.socket() as client:
        client.settimeout(.15)
        return client.connect_ex(('127.0.0.1',port))==0
def read_state(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'));return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}
def fresh(state):
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(state['at'])).total_seconds()<10 and state.get('status') in ('starting','running')
    except (KeyError,TypeError,ValueError):return False
def arguments(config,service):
    args=[sys.executable,'-u',str(Path(__file__).with_name('context_graph.py')),'--db',config['database'],'--codex-home',config['codexHome']]
    if config.get('claudeHome'):args+=['--claude-home',config['claudeHome']]
    args += [service['mode'],'--interval',str(config.get('interval',10)),'--live-dir',service['directory'],'--port',str(service['port'])]
    for field,flag in [('graphSnapshot','--graph-snapshot'),('mapSnapshot','--map-snapshot'),('tailscaleOrigin','--tailscale-origin'),('tailscaleUser','--tailscale-user')]:
        if service.get(field):args += [flag,service[field]]
    return args
def start(path):
    config=load_config(path);root,state_path,_=paths(path)
    with _locked_preferences(root/'lifecycle'):
        state=read_state(state_path)
        if fresh(state):return {'status':'already-running','pid':state.get('pid')}
        if any(connected(s['port']) for s in config['services']):raise ValueError('A configured port is already in use; no process was stopped')
        instance=uuid.uuid4().hex
        options={'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
        with (root/'supervisor.stdout.log').open('ab') as stdout,(root/'supervisor.stderr.log').open('ab') as stderr:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'run','--config',str(path),'--instance',instance],stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,**options)
        _launched[instance]=process
        atomic_write(state_path,json.dumps({'status':'starting','instance':instance,'pid':process.pid,'at':timestamp(),'services':[]}).encode())
        return {'status':'starting','pid':process.pid}
def stop(path,timeout=25):
    root,state_path,stop_path=paths(path);state=read_state(state_path)
    if not fresh(state):return {'status':'not-running'}
    atomic_write(stop_path,json.dumps({'instance':state['instance']}).encode())
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=read_state(state_path)
        if current.get('status')=='stopped' or current.get('instance')!=state['instance']:
            worker=_launched.pop(state['instance'],None)
            if worker is not None:worker.wait(timeout=5)
            return {'status':'stopped'}
        time.sleep(.2)
    raise TimeoutError('Supervisor did not stop; no unrelated process was terminated')
def status(path):
    config=load_config(path);root,state_path,_=paths(path);state=read_state(state_path)
    return {'status':state.get('status','not-running') if fresh(state) else 'not-running',
            'at':state.get('at'),'services':[{'name':s['name'],'port':s['port'],'listening':connected(s['port']),
             'process':next((x for x in state.get('services',[]) if x.get('name')==s['name']),None)} for s in config['services']]}
def run(path,instance):
    config=load_config(path);root,state_path,stop_path=paths(path)
    children={};handles={};attempts={s['name']:0 for s in config['services']};retry={s['name']:0 for s in config['services']}
    try:
        while read_state(stop_path).get('instance')!=instance:
            entries=[]
            for service in config['services']:
                name=service['name'];process=children.get(name)
                if process is None or process.poll() is not None:
                    if time.monotonic()>=retry[name]:
                        if connected(service['port']):
                            retry[name]=time.monotonic()+10;entries.append({'name':name,'state':'port-conflict'});continue
                        for stream in handles.pop(name,()):stream.close()
                        stdout=(root/(name+'.stdout.log')).open('ab');stderr=(root/(name+'.stderr.log')).open('ab');handles[name]=(stdout,stderr)
                        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                        process=subprocess.Popen(arguments(config,service),stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                                 env={**os.environ,'PYTHONUTF8':'1'},**options)
                        children[name]=process;attempts[name]+=1;retry[name]=time.monotonic()+min(60,2**min(attempts[name],6))
                entries.append({'name':name,'pid':process.pid if process else None,'state':'running' if process and process.poll() is None else 'retrying','starts':attempts[name]})
            atomic_write(state_path,json.dumps({'status':'running','instance':instance,'pid':os.getpid(),'at':timestamp(),'services':entries}).encode())
            time.sleep(1)
    finally:
        for process in children.values():
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        for streams in handles.values():
            for stream in streams:stream.close()
        atomic_write(state_path,json.dumps({'status':'stopped','instance':instance,'pid':os.getpid(),'at':timestamp(),'services':[]}).encode())
def autostart(path,enable):
    if os.name!='nt':raise ValueError('Login auto-start currently supports Windows')
    directory=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup'
    target=directory/'Session Atlas.lnk';root,_,_=paths(path);manifest=root/'autostart.json'
    prior=read_state(manifest)
    if target.exists() and prior.get('sha256')!=hashlib.sha256(target.read_bytes()).hexdigest():raise ValueError('Existing startup entry is not managed by this profile')
    if not enable:
        target.unlink(missing_ok=True);manifest.unlink(missing_ok=True);return {'autostart':False}
    load_config(path)
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.is_file():raise ValueError('A windowless Python executable is required')
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    arguments_text=subprocess.list2cmdline([str(Path(__file__).resolve()),'start','--config',str(path)])
    directory.mkdir(parents=True,exist_ok=True)
    script="$ErrorActionPreference='Stop';$s=(New-Object -ComObject WScript.Shell).CreateShortcut("+quote(target)+");$s.TargetPath="+quote(pythonw)+";$s.Arguments="+quote(arguments_text)+";$s.WorkingDirectory="+quote(Path(__file__).resolve().parent)+";$s.WindowStyle=7;$s.Description='Session Atlas background runtime';$s.Save()"
    encoded=base64.b64encode(script.encode('utf-16-le')).decode()
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not target.is_file():raise OSError('Startup shortcut could not be created')
    atomic_write(manifest,json.dumps({'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'profile':str(path)}).encode())
    legacy=directory/'Session Atlas.vbs'
    if legacy.exists():
        try:content=legacy.read_text(encoding='utf-16')
        except UnicodeError:content=''
        if content.startswith("' Session Atlas managed login startup") and str(path).casefold() in content.casefold():legacy.unlink()
    return {'autostart':True,'trigger':'current-user-login','method':'windowless-python-shortcut'}
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['start','stop','restart','status','run','enable-autostart','disable-autostart'])
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--instance')
    args=parser.parse_args();path=args.config.expanduser().resolve()
    try:
        if args.action=='run':
            if not args.instance or len(args.instance)!=32:raise ValueError('Missing instance')
            run(path,args.instance);return 0
        if args.action=='restart':stop(path);result=start(path)
        elif args.action=='start':result=start(path)
        elif args.action=='stop':result=stop(path)
        elif args.action=='status':result=status(path)
        else:result=autostart(path,args.action=='enable-autostart')
        print(json.dumps(result));return 0
    except (OSError,ValueError,TimeoutError) as error:
        print('Runtime operation failed ('+type(error).__name__+'); configuration and local logs need review',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
