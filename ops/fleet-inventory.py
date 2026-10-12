"""Read-only migration inventory. Never emit credentials or producer configuration."""
import hashlib
import json
import os
import pathlib
import plistlib
import pwd
import sqlite3
import subprocess
import time

def run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=12)
        return {"code": p.returncode, "output": p.stdout.strip()}
    except Exception as e:
        return {"code": -1, "error": type(e).__name__}

home = pathlib.Path.home()
legacy = home / '.openclaw/fleet-node-observability'
root = home / '.local/share/fleet-node'
report = {"at": time.time(), "user": pwd.getpwuid(os.getuid()).pw_name,
          "uid": os.getuid(), "home": str(home), "arch": os.uname().machine,
          "os": run(['/usr/bin/sw_vers', '-productVersion']), "services": [], "runtimes": []}
for folder, domain in [('/Library/LaunchDaemons','system'), (str(home/'Library/LaunchAgents'), 'gui/'+str(os.getuid()))]:
    for p in pathlib.Path(folder).glob('*.plist'):
        try:
            data = plistlib.loads(p.read_bytes())
        except Exception:
            continue
        args = data.get('ProgramArguments', [])
        # Selected fleet service args contain paths, never authorization headers.
        if data.get('Label','').startswith('com.unblocklabs.') and any(s in p.name for s in ['fleet-node','node-exporter','textfile']):
            status = run(['/bin/launchctl','print',domain+'/'+data['Label']])
            report['services'].append({'path':str(p),'domain':domain,'label':data['Label'],
                'sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'uid':p.stat().st_uid,
                'user':data.get('UserName'), 'args':args,
                'loaded':status['code']==0, 'pid':next((s.strip() for s in status.get('output','').splitlines() if s.strip().startswith('pid = ')),None)})
        elif str(legacy) in json.dumps(data):
            report.setdefault('sharedLegacyReferences', []).append({'path':str(p),'label':data.get('Label')})
for node,npm in [('/opt/homebrew/opt/node@24/bin/node','/opt/homebrew/opt/node@24/lib/node_modules/npm/bin/npm-cli.js'),('/opt/homebrew/bin/node','/opt/homebrew/lib/node_modules/npm/bin/npm-cli.js'),('/usr/local/bin/node','/usr/local/lib/node_modules/npm/bin/npm-cli.js')]:
    if pathlib.Path(node).exists():
        report['runtimes'].append({'node':node,'npm':npm,'realNode':os.path.realpath(node),
            'version':run([node,'--version']),'npmExists':pathlib.Path(npm).exists()})
report['executables']={name:[str(p) for p in [home/'.npm-global/bin'/name,home/'.local/bin'/name,pathlib.Path('/opt/homebrew/bin')/name,pathlib.Path('/usr/local/bin')/name] if p.exists()] for name in ['openclaw','codex']}
for key,path in [('legacy',legacy),('target',root),('prefix',home/'.local/fleet-node-npm'),('staging',home/'.local/share/fleet-node-migration-20260928')]:
    report[key]={'path':str(path),'exists':path.exists(),'symlink':path.is_symlink()}
report['configFiles']=[str(p) for p in (legacy/'config').glob('*') if p.is_file()]
report['credential']={'exists':(legacy/'secrets/authorization-header').is_file(),'mode':oct((legacy/'secrets/authorization-header').stat().st_mode & 0o777) if (legacy/'secrets/authorization-header').exists() else None}
report['collectorVersion']=run([str(legacy/'bin/otelcol-contrib'),'--version'])
report['freeBytes']=os.statvfs(home).f_bavail*os.statvfs(home).f_frsize
ledger=legacy/'state/sessions/starts.sqlite'
if ledger.is_file():
    try:
        db=sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True,timeout=2)
        report['ledger']={'count':db.execute('SELECT count(*) FROM starts').fetchone()[0],'observedSince':db.execute("SELECT value FROM metadata WHERE key='observed_since'").fetchone()[0]}
        db.close()
    except Exception as e:
        report['ledger']={'error':type(e).__name__}
else:
    report['ledger']={'missing':True}
print(json.dumps(report))
