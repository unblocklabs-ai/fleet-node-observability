"""One-time, explicit September fleet preparation. Never executes cutover or sudo."""
import concurrent.futures
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / 'artifacts/fleet-staging-20260928'
HOSTS = {'pearlperelel':'pearl','veraperelel':'vera','james-fga':'james','rockys-mini-2':'rocky',
         'fionaperelel':'fiona','fred':'fred','bridger':'bridger','fredfarryn':'fred_farrynheight',
         'mac-thesis-theo':'theo','ivywillow':'ivy','alfie':'alfie'}
NODE_ARCHIVE = pathlib.Path('/tmp/fleet-node-runtime.5bZq9Y/node.tar.gz')
NODE_SHA = 'bed7eea5325e1108f32ce5228ddd6a5f0f08a499ee42aa7442aea583702f6057'
q = shlex.quote

def command(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=300, **kw).stdout

def ssh(host, cmd, **kw):
    return command(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12',host,cmd],**kw)

def write(path, content):
    path.write_text(content); path.chmod(0o600)

def build(host, config):
    inventory=json.loads((OUT/(host+'.inventory.json')).read_text())
    assert inventory['arch']=='arm64' and inventory['credential']['mode']=='0o600'
    assert inventory['collectorVersion']['output']=='otelcol-contrib version 0.157.0'
    assert not inventory.get('sharedLegacyReferences')
    assert inventory['freeBytes'] > 2*1024**3
    for key in ['target','prefix','staging']:
        assert not inventory[key]['exists'] and not inventory[key]['symlink']
    home=inventory['home']; user=inventory['user']; uid=inventory['uid']
    runtime=next((r for r in inventory['runtimes'] if r['node']=='/opt/homebrew/opt/node@24/bin/node' and re.fullmatch(r'v24\.(?:1[89]|[2-9][0-9])\.\d+',r['version'].get('output','')) and r['npmExists']),None)
    portable=runtime is None
    runtime_root=home+'/.local/share/fleet-node-runtime'
    node=runtime['node'] if runtime else runtime_root+'/bin/node'
    npm=runtime['npm'] if runtime else runtime_root+'/lib/node_modules/npm/bin/npm-cli.js'
    stage=inventory['staging']['path']; root=inventory['target']['path'];prefix=inventory['prefix']['path'];legacy=inventory['legacy']['path']
    services=[s for s in inventory['services'] if s['label']!='com.unblocklabs.node-exporter-proxy']
    assert len(services)==(7 if config['codex_usage_enabled'] else 6)
    assert all(s['domain']=='system' and s['loaded'] and s['uid']==0 and s['user']==user for s in services)
    settings={'nodePath':node,'npmCliPath':npm,'npmPrefix':prefix,'nodeHome':home,'openclawRoot':home+'/.openclaw',
              'openclawBin':inventory['executables']['openclaw'][0],'codexBin':inventory['executables']['codex'][0]}
    p={'host':host,'home':home,'user':user,'uid':uid,'node':node,'npm':npm,'stage':stage,'root':root,'prefix':prefix,'legacy':legacy,
       'cli':prefix+'/lib/node_modules/@unblocklabs/fleet-node/dist/cli.js','config':config,'settings':settings,'services':services,
       'observedSince':inventory['ledger']['observedSince'],'portableRuntime':portable,'guards':{}}
    directory=OUT/host;directory.mkdir(mode=0o700,exist_ok=True)
    write(directory/'plan.json',json.dumps(p,indent=2)+'\n')
    write(directory/'node.json',json.dumps(config,indent=2)+'\n')
    for name in ['fleet-precheck.mjs','fleet-preflight.mjs']:
        write(directory/name,(REPO/'ops'/name).read_text())
    importer=(REPO/'ops/import-bill-state.mjs').read_text().replace('/Users/billjohansson',home).replace('fleet-node-ts-canary-20260927','.local/share/fleet-node-migration-20260928').replace('process.getuid(), 501','process.getuid(), '+str(uid))
    write(directory/'import-state.mjs',importer)
    labels=' '.join(s['label'] for s in services)
    as_user=f'/usr/bin/sudo -H -u {q(user)}'
    precheck=f'{q(node)} {q(stage+"/fleet-precheck.mjs")}'
    pattern=re.escape(legacy)+r'/(config/collector[.]json|python/|bin/(fleet-node-agent-heartbeat|openclaw-gateway-health))|/opt/homebrew/var/lib/node_exporter/textfile_collector'
    header=f'''#!/bin/sh
set -eu
umask 077
[ "$(/usr/bin/uname -s)" = Darwin ]
[ "$(/usr/bin/id -u {q(user)})" = {uid} ]
rollout={q(stage)}
root={q(root)}
node={q(node)}
cli={q(p['cli'])}
'''
    cutover=header+f'''# --check is read-only and runs as the ordinary node account.
if [ "${{1:-}}" = --check ]; then exec {precheck}; fi
[ "$#" = 0 ]
[ "$(/usr/bin/id -u)" = 0 ] || {{ echo 'Run this cutover with sudo' >&2; exit 1; }}
{as_user} {precheck}
for label in {labels}; do
  /bin/launchctl disable system/$label
  if /bin/launchctl print system/$label >/dev/null 2>&1; then /bin/launchctl bootout system/$label; fi
  if [ -f /Library/LaunchDaemons/$label.plist ]; then
    /bin/mv /Library/LaunchDaemons/$label.plist "$rollout/rollback/$label.plist"
  fi
done
old_process_pattern={q(pattern)}
attempt=0
while /usr/sbin/lsof -nP -iTCP:9100 -iTCP:4318 -iTCP:8888 -iTCP:13133 -sTCP:LISTEN >/dev/null 2>&1 || /usr/bin/pgrep -u {uid} -f "$old_process_pattern" >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 40 ] || {{ echo 'Legacy processes did not quiesce. No state was copied; use rollback.sh.' >&2; exit 1; }}
  /bin/sleep 1
done
for label in {labels}; do
  if /bin/launchctl print system/$label >/dev/null 2>&1; then exit 1; fi
  /bin/launchctl print-disabled system | /usr/bin/grep -F "\\\"$label\\\" => disabled" >/dev/null
done
{as_user} "$node" "$rollout/import-state.mjs"
/bin/sh "$root/services/install-system.sh"
{as_user} "$node" "$cli" start --root "$root"
/usr/bin/touch "$rollout/cutover-complete"
/usr/sbin/chown {q(user)}:staff "$rollout/cutover-complete"
echo '{config['node_label']}: cutover complete; old files retained. Verify fresh local and central telemetry.'
'''
    write(directory/'cutover.sh',cutover)
    rollback=header+f'''[ "$#" = 0 ]
[ "$(/usr/bin/id -u)" = 0 ] || {{ echo 'Run rollback with sudo' >&2; exit 1; }}
[ -d "$rollout/rollback" ] && [ ! -L "$rollout/rollback" ]
if [ -e "$root/services/enabled" ] || [ -L "$root/services/enabled" ]; then
  [ -f "$root/services/enabled" ] && [ ! -L "$root/services/enabled" ]
  /bin/mv "$root/services/enabled" "$rollout/rollback/new-enabled-before-return-$(/bin/date +%s)"
fi
for job in collector exporter heartbeat readiness thermal cron sessions codex; do
  /bin/launchctl disable system/com.unblocklabs.fleet-node-ts.$job
  if /bin/launchctl print system/com.unblocklabs.fleet-node-ts.$job >/dev/null 2>&1; then /bin/launchctl bootout system/com.unblocklabs.fleet-node-ts.$job; fi
done
attempt=0
while /usr/sbin/lsof -nP -iTCP:9100 -iTCP:4318 -iTCP:8888 -iTCP:13133 -sTCP:LISTEN >/dev/null 2>&1 || /usr/bin/pgrep -u {uid} -f {q(re.escape(prefix)+'/|'+re.escape(root)+'/')} >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 40 ] || {{ echo 'New runtime not quiescent; do not restore state.' >&2; exit 1; }}
  /bin/sleep 1
done
if [ -e "$rollout/cutover-state-preserved.json" ] && [ ! -e "$rollout/rollback-state-preserved.json" ]; then
  {as_user} "$node" "$rollout/import-state.mjs" --reverse
fi
for label in {labels}; do
  source="$rollout/rollback/$label.plist"
  target=/Library/LaunchDaemons/$label.plist
  if [ -e "$source" ]; then
    [ -f "$source" ] && [ ! -L "$source" ] && [ ! -e "$target" ] && [ ! -L "$target" ]
    /bin/mv "$source" "$target"
    /usr/sbin/chown root:wheel "$target"
    /bin/chmod 644 "$target"
  fi
  [ -f "$target" ] && [ ! -L "$target" ]
  /bin/launchctl enable system/$label
  if ! /bin/launchctl print system/$label >/dev/null 2>&1; then /bin/launchctl bootstrap system "$target"; fi
done
echo 'Legacy services restored; verify telemetry. New state and pre-return backups retained.'
'''
    write(directory/'rollback.sh',rollback)
    for name in ['cutover.sh','rollback.sh']: command(['/bin/sh','-n',str(directory/name)])
    return p,directory

def stage_one(host,config):
    p,directory=build(host,config)
    try:
        stage=p['stage']; root=p['root']; prefix=p['prefix']; home=p['home']; node=p['node']; npm=p['npm']
        # All targets were read-only inventoried absent. Fail rather than reuse unknown files.
        initial=f'set -eu; umask 077; [ "$(id -u)" = {p["uid"]} ]; '
        for path in [stage,root,prefix]+([home+'/.local/share/fleet-node-runtime'] if p['portableRuntime'] else []):
            initial+=f'[ ! -e {q(path)} ] && [ ! -L {q(path)} ]; '
        initial+=f'mkdir -p {q(stage+"/rollback")}; chmod 700 {q(stage)} {q(stage+"/rollback")}'
        ssh(host,initial)
        command(['scp',*[str(x) for x in directory.iterdir() if x.is_file()],host+':'+stage+'/'])
        if p['portableRuntime']:
            assert hashlib.sha256(NODE_ARCHIVE.read_bytes()).hexdigest()==NODE_SHA
            command(['scp',str(NODE_ARCHIVE),host+':'+stage+'/node-v24.21.0.tar.gz'])
            runtime_root=home+'/.local/share/fleet-node-runtime'
            ssh(host,f'set -eu; umask 077; [ "$(/usr/bin/shasum -a 256 {q(stage+"/node-v24.21.0.tar.gz")} | cut -d " " -f 1)" = {NODE_SHA} ]; mkdir {q(runtime_root)}; /usr/bin/tar -xzf {q(stage+"/node-v24.21.0.tar.gz")} --strip-components=1 -C {q(runtime_root)}')
        settings=p['settings']
        prep=f'''set -eu
umask 077
export PATH={q(str(pathlib.Path(node).parent)+':/usr/bin:/bin:/usr/sbin:/sbin')}
export npm_config_update_notifier=false
{q(node)} {q(npm)} install --global --prefix {q(prefix)} --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org @unblocklabs/fleet-node@0.1.0
{q(node)} {q(p['cli'])} init --root {q(root)} --config {q(stage+'/node.json')} --node {q(node)} --npm-cli {q(npm)} --prefix {q(prefix)} --openclaw {q(settings['openclawBin'])} --codex {q(settings['codexBin'])} --authorization-file {q(p['legacy']+'/secrets/authorization-header')}
{q(node)} {q(p['cli'])} prepare --root {q(root)}
{q(node)} {q(p['cli'])} validate --root {q(root)}
{q(node)} {q(stage+'/fleet-preflight.mjs')}
'''
        result=ssh(host,'/bin/sh -s',input=prep)
        write(directory/'prepare-output.txt',result)
        # Pin staged scripts, package JS, and service inputs after preparation. Do not
        # pin live SQLite/queue bytes: they must continue changing before cutover.
        guard_script="""import json,pathlib,hashlib
p=pathlib.Path(%r);d=json.loads(p.read_text());files=[pathlib.Path(d['legacy'])/'config/collector.json',pathlib.Path(d['home'])/'.openclaw/openclaw.json']
files+=list(p.parent.glob('*.sh'))+list(p.parent.glob('*.mjs'))
files+=list((pathlib.Path(d['root'])/'services').glob('*.sh'))+list((pathlib.Path(d['root'])/'services').glob('*.plist'))
files+=list((pathlib.Path(d['prefix'])/'lib/node_modules/@unblocklabs/fleet-node/dist').glob('*.js'))
files+=[pathlib.Path(d['root'])/'config/collector.json']
d['guards']={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files};p.write_text(json.dumps(d,indent=2)+'\\n');p.chmod(0o600)
print(json.dumps({'guardedFiles':len(files)}))
""" % (stage+'/plan.json')
        guard_result=ssh(host,'/usr/bin/python3 -',input=guard_script)
        check=ssh(host,'/bin/sh '+q(stage+'/cutover.sh')+' --check')
        command(['scp',host+':'+stage+'/plan.json',str(directory/'plan.json')])
        command(['scp',host+':'+stage+'/preflight-report.json',str(directory/'preflight-report.json')])
        return {'host':host,'node':config['node_label'],'status':'staged-not-migrated','precheck':check.strip(),'guards':json.loads(guard_result),'command':'ssh -t '+host+' '+q('sudo /bin/sh '+stage+'/cutover.sh')}
    except subprocess.CalledProcessError as e:
        write(directory/'failure.txt',e.stdout+'\n'+e.stderr)
        return {'host':host,'status':'blocked-before-cutover','exit':e.returncode,'evidence':str(directory/'failure.txt')}
    except Exception as e:
        return {'host':host,'status':'blocked-before-cutover','error':str(e)}

if __name__=='__main__':
    assert sys.argv[1:]==['--stage'], 'Use --stage only after explicit fleet preparation approval'
    configs=json.loads(ssh('charizard',"python3 -c 'import pathlib,json;print(json.dumps({p.stem:json.loads(p.read_text()) for p in pathlib.Path(\"/home/bek/fleet-observability-runtime/node-config\").glob(\"*.json\")}))'"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(stage_one,h,configs[label]):h for h,label in HOSTS.items()}
        results=[]
        for future in concurrent.futures.as_completed(futures):
            result=future.result();results.append(result);print(json.dumps(result),flush=True)
            write(OUT/'staging-results.json',json.dumps(results,indent=2)+'\n')
