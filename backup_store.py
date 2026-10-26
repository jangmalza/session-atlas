"""Verified local backups. Restore always creates a new database, never overwrites."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,tempfile,zipfile
from pathlib import Path
from topic_display import _locked_preferences

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def backup(database:Path,output:Path,graph_snapshot:Path|None=None):
    database=database.resolve();output=output.resolve()
    if output.exists():raise FileExistsError('Backup already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    preference=database.with_suffix('.topic-display.json')
    with tempfile.TemporaryDirectory(prefix='atlas-backup-',dir=output.parent) as directory:
        root=Path(directory);copy=root/'database.sqlite'
        with _locked_preferences(preference),closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(copy)) as target:
            source.execute('PRAGMA query_only=ON');source.backup(target)
            files={'database.sqlite':copy}
            if preference.exists():
                saved=root/'display.json';saved.write_bytes(preference.read_bytes());files['display.json']=saved
            if graph_snapshot:
                from live_dashboard import load_graph
                projection=root/'projection.json';projection.write_text(json.dumps(load_graph(graph_snapshot),ensure_ascii=False),encoding='utf-8')
                files['projection.json']=projection
        manifest={'version':1,'createdUtc':datetime.now(timezone.utc).isoformat(),'files':{name:sha(path) for name,path in files.items()}}
        archive=root/'backup.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,path in files.items():z.write(path,name)
            z.writestr('manifest.json',json.dumps(manifest))
        with output.open('xb') as dst,archive.open('rb') as src:
            for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
    return {'backup':str(output),'files':len(files)}
def restore(archive:Path,output:Path):
    output=output.resolve();preference=output.with_suffix('.topic-display.json');projection=output.with_suffix('.graph.json')
    if output.exists() or preference.exists() or projection.exists():raise FileExistsError('Restore requires a new destination')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='atlas-restore-',dir=output.parent) as directory:
        root=Path(directory)
        with zipfile.ZipFile(archive) as z:
            names=z.namelist()
            if len(names)!=len(set(names)) or not set(names)<={'database.sqlite','display.json','projection.json','manifest.json'}:raise ValueError('Unexpected backup entries')
            if not {'database.sqlite','manifest.json'}<=set(names):raise ValueError('Incomplete backup')
            if z.getinfo('manifest.json').file_size>16384:raise ValueError('Invalid manifest')
            manifest=json.loads(z.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('version')!=1 or not isinstance(manifest.get('files'),dict) or set(manifest['files'])!=set(names)-{'manifest.json'}:raise ValueError('Invalid backup manifest')
            for name,digest in manifest['files'].items():
                path=root/name
                with z.open(name) as src,path.open('xb') as dst:
                    for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
                if sha(path)!=digest:raise ValueError('Backup checksum mismatch')
        with closing(sqlite3.connect((root/'database.sqlite').as_uri()+'?mode=ro',uri=True)) as con:
            con.execute('PRAGMA query_only=ON')
            if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Invalid database')
            if not {'sessions','messages','session_maps'}<={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:raise ValueError('Not a Session Atlas database')
        if (root/'projection.json').exists():
            from live_dashboard import load_graph
            load_graph(root/'projection.json')
        # Exclusive creation protects another process that claimed the path.
        created=[]
        try:
            for source,dest in [(root/'database.sqlite',output),(root/'display.json',preference),(root/'projection.json',projection)]:
                if not source.exists():continue
                with dest.open('xb') as dst,source.open('rb') as src:
                    created.append(dest)
                    for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
        except Exception:
            for dest in created:dest.unlink(missing_ok=True)
            raise
    return {'restored':str(output),'originalOverwritten':False}
