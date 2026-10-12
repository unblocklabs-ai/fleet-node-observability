"""Read-only central receipt check. Caller supplies CUTOVERS (node -> Unix time).
Never outputs raw log bodies, span attributes, credential values or transcripts.
"""
import concurrent.futures
import json
import time
import urllib.parse
import urllib.request

def get(port,path,params):
    url='http://127.0.0.1:'+str(port)+path+'?'+urllib.parse.urlencode(params)
    with urllib.request.urlopen(url,timeout=25) as response:return json.load(response)

def prom(query):return get(9090,'/api/v1/query',{'query':query})['data']['result']

now=time.time()
names=['fleet_node_agent_heartbeat_timestamp_seconds','fleet_node_agent_queue_metrics_available',
       'openclaw_gateway_ready','openclaw_gateway_check_timestamp_seconds','fleet_macos_thermal_collector_success',
       'fleet_macos_thermal_collected_at_seconds','openclaw_cron_schedule_collector_success',
       'openclaw_cron_schedule_collected_at_seconds','openclaw_sessions_collector_success',
       'openclaw_sessions_collected_at_seconds','codex_collector_success','codex_usage_collected_at_seconds']
nodes={node:{'cutover':cutover,'signals':{},'queues':{},'alerts':[]} for node,cutover in CUTOVERS.items()}
for x in prom('{node=~"'+'|'.join(nodes)+'",__name__=~"'+'|'.join(names)+'"}'):
    n=x['metric']['node'];name=x['metric']['__name__'];value=float(x['value'][1])
    nodes[n]['signals'][name]={'value':value,'errorType':x['metric'].get('error_type')}
for key,q in {
    'hostAge':'time()-max by(node)(timestamp(node_cpu_seconds_total))',
    'selfAge':'time()-max by(node)(timestamp(otelcol_exporter_queue_size))',
    'nativeMetricsAge':'time()-max by(node)(timestamp(openclaw_gateway_rpc_requests_total))',
}.items():
    for x in prom(q):
        n=x['metric'].get('node')
        if n in nodes:nodes[n][key]=float(x['value'][1])
for x in prom('otelcol_exporter_queue_size'):
    n=x['metric'].get('node')
    if n in nodes:nodes[n]['queues'][x['metric']['exporter']]=float(x['value'][1])
for x in prom('ALERTS{alertstate=~"pending|firing",alertname=~".*(Stale|Queue|GatewayNotReady).*"}'):
    n=x['metric'].get('node')
    if n in nodes:nodes[n]['alerts'].append({'name':x['metric']['alertname'],'state':x['metric']['alertstate']})

def receipts(item):
    node,cutover=item;duration=max(1,int(now-cutover));result={}
    try:
        q='sum(count_over_time({telemetry_domain="openclaw_fleet",node="'+node+'"}['+str(duration)+'s]))'
        data=get(3100,'/loki/api/v1/query',{'query':q,'time':str(now)})['data']['result']
        result['postCutoverLogs']=sum(float(x['value'][1]) for x in data)
    except Exception as e:result['logQueryError']=type(e).__name__
    try:
        search=get(3200,'/api/search',{'q':'{ resource.node = "'+node+'" && name =~ "openclaw.*" }','start':int(cutover)+1,'end':int(now),'limit':1})
        result['postCutoverTracesFound']=len(search.get('traces',[]))
        if search.get('traces'):
            trace=get(3200,'/api/traces/'+search['traces'][0]['traceID'],{})
            # Tempo OTLP JSON uses batches in this deployed version.
            batches=trace.get('batches',trace.get('resourceSpans',[]))
            spans=[]
            for batch in batches:
                attrs={a['key']:a.get('value',{}).get('stringValue') for a in batch.get('resource',{}).get('attributes',[])}
                if attrs.get('node')!=node:continue
                for scope in batch.get('scopeSpans',batch.get('instrumentationLibrarySpans',[])):
                    for span in scope.get('spans',[]):
                        timestamp=float(span.get('startTimeUnixNano',0))/1e9
                        if timestamp>cutover and span.get('name','').startswith('openclaw.'):
                            spans.append({'name':span['name'],'start':timestamp,'source':attrs.get('fleet_signal_source')})
            result['verifiedNativeSpans']=spans[:5]
    except Exception as e:result['traceQueryError']=type(e).__name__
    return node,result

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    for node,result in pool.map(receipts,CUTOVERS.items()):nodes[node].update(result)
print(json.dumps({'at':now,'nodes':nodes}))
