"""In-process desktop workers. No task ownership is inferred from configuration."""
import json, os, queue, sqlite3, threading, time, sys
from conversation import read_conversation
from codex_history import read_codex_conversation
from codex_activity import CodexActivity
from codex_watch import RolloutWatch, SharedCodex, completion, completion_from_rpc
from codex_workspace import CodexWorkspace, recent, RETENTION
from pathlib import Path
from automation import Approvals, Scheduler
from channels_dingtalk import DingTalkWebhook
from rpc_client import RPC
from managed_tasks import ManagedTasks
from reply_log import ReplyLog
from daemon_support import ScopedPolicy,WindowsNotifications
from attachments import Attachments
from task_control import TaskControl
from history_labels import load_labels
from coordination_client import device_client
from personal_relay import RemoteRecords
from rpc_errors import RPCError
from code_sync import github_id
from ide_activity import close_queue, flush_closes
from tray_release import CodexTrayRelease, TrayRelease, release_step
def _runtime_root():
 # A frozen app must keep its databases/configuration beside the EXE.  PyInstaller's
 # temporary extraction directory is disposable and cannot be used for state.
 if getattr(sys, 'frozen', False):
  return Path(sys.executable).resolve().parent
 return Path(__file__).resolve().parent

PROGRAM_ROOT=_runtime_root()
ROOT=PROGRAM_ROOT
_workspace=None
_workspace_online=True

def set_workspace_online(enabled):
 global _workspace_online
 _workspace_online=enabled

def prepare_desktop_data(select_legacy,source_override=None):
 global ROOT,_workspace
 if not getattr(sys,'frozen',False):return True
 from desktop_storage import prepare_data,user_data_root
 destination=user_data_root()
 workspace=prepare_data(PROGRAM_ROOT,destination,select_legacy,source_override)
 if workspace is None:return False
 ROOT=destination
 _workspace=workspace
 return True

approvals=Approvals()
workspace_codex=CodexWorkspace(approvals)
codex_completions=queue.SimpleQueue()
state={'serviceVersion':'0.7.3-desktop-only','pid':os.getpid(),'updated':None,'quota':None,'quotaStatus':'unknown','threads':[],'schedule':[], 'connection':'starting','notification':'not_configured','wake':{'mode':'guarded','reason':'Only connection-owned threads with active goals are eligible.'},'claude':{'connection':'starting','threads':[],'controls':{'tasks':[],'operations':[]},'quotaStatus':'unknown','quota':None,'schedule':[]}}
state_lock=threading.Lock()
controller=None
claude_controller=None
claude_rpc=None
claude_journal=None
history_labels={}
personal_sync=None
codex_activity=CodexActivity()
tray_release=TrayRelease()

def set_tray_mode(hidden):
 tray_release.set(bool(hidden))

def tray_release_status():
 return tray_release.status()

def enable_personal_sync(settings, stop, workers):
 global personal_sync
 from personal_sync import PersonalSync
 from temporary_files import FileExchange
 if personal_sync is not None:
  raise ValueError('个人同步已启动')
 personal_sync=PersonalSync(ROOT,settings,config().get('coordination'))
 personal_sync.file_exchange=FileExchange(ROOT,personal_sync)
 thread=threading.Thread(target=personal_sync.worker,args=(stop,),name='personal-sync',daemon=True)
 workers.threads.append(thread)
 thread.start()
 activity=threading.Thread(target=personal_sync.watch_task_owners,args=(stop,),name='personal-task-owners',daemon=True)
 workers.threads.append(activity)
 activity.start()
 transfer=threading.Thread(target=personal_sync.file_exchange.run,args=(stop,),name='file-exchange',daemon=True)
 workers.threads.append(transfer)
 transfer.start()


def personal_settings(settings):
 if not _workspace_online:return None
 personal=settings.get('personalSync')
 pair_file=ROOT/'personal-sync.json'
 if pair_file.is_file():
  personal={**(personal or {}),**json.loads(pair_file.read_text(encoding='utf-8-sig'))}
 if (settings.get('coordination') or {}).get('protectedToken'):
  personal={**(personal or {}),'exchange':None,'codeOnly':False,'memory':True}
 return personal


def shared_approval_coordination():
 settings=config()
 client=device_client(settings)
 personal=personal_settings(settings) if client else None
 return client, personal is not None and not personal.get('exchange') and not personal.get('codeOnly')


def activate_cloud_pairing(stop, workers):
 settings=config()
 personal=personal_settings(settings)
 if personal_sync is None:
  enable_personal_sync(personal,stop,workers)
 else:
  personal_sync.reconfigure(personal,settings['coordination'])

def client_snapshot(snap,client='codex'):
 if client not in ('codex','claude'):raise ValueError('无效客户端。')
 if client=='codex':
  result={k:v for k,v in snap.items() if k!='claude'}
 else:
  result=dict(snap.get('claude') or {'connection':'starting','threads':[],'controls':{'tasks':[],'operations':[]},'quota':None,'quotaStatus':'unknown','schedule':[]})
 result['client']=client
 result['approvals']=[a for a in approvals.public() if a.get('client','codex')==client]
 if client=='codex':result['approvals']+=workspace_codex.pending()
 return result


def approval_queue(client=None):
 local=[{**item,'remote':False,'deviceName':'本机','decisionQueued':False} for item in approvals.pending(client)]
 return local+(workspace_codex.pending() if client in (None,'codex') else [])


def approval_snapshot():
 with state_lock:
  shared=state.get('codexApprovalWatch','own_connection')
  workspace=state.get('codexWorkspaceWatch','local')
 return {'pending':approval_queue(),'decisions':workspace_codex.decisions(),'sharedCodex':shared,'workspace':workspace}


def decide_approvals(tokens,allow):
 if type(allow) is not bool or not isinstance(tokens,(list,tuple)) or not all(isinstance(token,str) for token in tokens):
  raise ValueError('请提供明确的审批项目和决定。')
 return [{'token':token,'decision':workspace_codex.decide(token,allow) if token.startswith('remote:')
          else approvals.decide_pending(token,allow)} for token in dict.fromkeys(tokens)]


def codex_completed(params):
 item=completion_from_rpc(params)
 if item:codex_completions.put(item)

def get_controller(client='codex'):
 if client not in ('codex','claude'):raise ValueError('无效客户端。')
 return claude_controller if client=='claude' else controller

def conversation_for(thread_id,client='codex',*,local_history=True):
 if client not in ('codex','claude'):raise ValueError('无效客户端。')
 if client=='claude':
  return claude_rpc.conversation(thread_id) if claude_rpc else {'messages':[],'events':[]}
 path=ROOT/'replies.sqlite3'
 if local_history:
  return read_codex_conversation(path,thread_id,activity=codex_activity.snapshot(thread_id))
 return read_conversation(path,thread_id)
def snapshot():
 with state_lock:result=json.loads(json.dumps(state))
 # Control intent and fast interrupts can change between polling rounds. Read the
 # short, DB-only snapshot so a slow RPC never leaves Pause/Send falsely enabled.
 for client,target in (('codex',controller),('claude',claude_controller)):
  if target is None or not hasattr(target,'snapshot'):continue
  try:controls=target.snapshot()
  except sqlite3.Error:continue
  view=result if client=='codex' else result.setdefault('claude',{})
  view['controls']=controls;view['attachments']=controls['tasks']
 return result
def update(**values):
 with state_lock:state.update(values)
def saved_config():
 path=ROOT/'local-config.json'
 return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
def config():
 settings=saved_config()
 if not _workspace_online:
  return {key:value for key,value in settings.items() if key not in ('coordination','personalSync','handoffSync')}
 return settings
def clean_limits(x):
 def window(w):return {k:w.get(k) for k in ('usedPercent','windowDurationMins','resetsAt')} if isinstance(w,dict) else None
 def one(v):return {**{key:v.get(key) for key in ('spendControlReached','rateLimitReachedType','individualLimit')},'primary':window(v.get('primary')),'secondary':window(v.get('secondary'))}
 return {'ordinaryUsageAllowed':x.get('ordinaryUsageAllowed'),'default':one(x.get('rateLimits') or {}),'models':{k:one(v) for k,v in (x.get('rateLimitsByLimitId') or {}).items()}}
def collect(rpc,cfg,scheduler):
 approvals.expire()
 # Read current backend quota before any scheduler decision. Reset times are only
 # wake-up hints; ordinaryUsageAllowed must explicitly confirm recovery.
 quota=None
 try:
  quota=rpc.call('account/rateLimits/read',{})
  update(quota=clean_limits(quota),quotaStatus='official_api')
 except Exception:update(quota=None,quotaStatus='unknown: endpoint unavailable')
 schedule_result=scheduler.tick(rpc,cfg.get('tasks',[]),quota=quota)
 update(quotaWaits=scheduler.quota_recovery.snapshot())
 if schedule_result:update(schedule=schedule_result,lastScheduleCheck=scheduler.last_check)
 try:
  data=rpc.call('thread/list',{'limit':20})
  names=((snapshot().get('controls') or {}).get('names') or {})
  update(threads=[{'id':t['id'],'title':names.get(t['id']) or t.get('name') or t.get('title') or history_labels.get(t['id'],{}).get('title') or '未命名历史任务','source':history_labels.get(t['id'],{}).get('source'),'cwd':t.get('cwd') or history_labels.get(t['id'],{}).get('cwd'),'githubProject':github_id((t.get('gitInfo') or {}).get('originUrl')),'status':t.get('status',{'type':'unknown'}),'updatedAt':t.get('updatedAt')} for t in data.get('data',[])])
 except Exception:update(threads=[],threadStatus='unavailable')
 update(updated=int(time.time()),connection='disconnected' if rpc.closed else 'connected')
class Notifications:
 def __init__(self,path):
  self.db=sqlite3.connect(path)
  self.db.execute('CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,status TEXT)');self.db.commit()
 def deliver(self,channel,event,title,body):
  # Reserve before POST. Uncertain delivery is retained, never blindly retried.
  cur=self.db.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?)',(event,'reserved'));self.db.commit()
  if not cur.rowcount:return 'duplicate'
  result=channel.send(event,title,body)['status']
  self.db.execute('UPDATE deliveries SET status=? WHERE id=?',(result,event));self.db.commit()
  return result

def poll(stop):
 global controller,history_labels
 scheduler=Scheduler(str(ROOT/'scheduler.sqlite3'),approvals)
 notifications=Notifications(str(ROOT/'notifications.sqlite3'))
 workspace=_workspace or ROOT.parent
 managed=ManagedTasks(str(ROOT/'managed.sqlite3'),workspace)
 rpc=None;channel=None;failures=0;drained=[]
 release_generation=None;handoff=None
 controller=TaskControl(ROOT/'task-control.sqlite3');controller.seed(config().get('autoAttachThreads',[]))
 controller.control_allowed=tray_release.control_allowed
 journal=ReplyLog(str(ROOT/'replies.sqlite3'),activity=codex_activity,reply_notifications=False)
 native=WindowsNotifications(str(ROOT/'windows-notifications.sqlite3'))
 try:
  while not stop.is_set():
   try:
    cfg=config()
    generation=tray_release.begin('codex')
    if generation is not None:
     result=('waiting',0,'本机任务暂未完成交还，后台将重试。')
     try:
      if generation != release_generation:
       controller.disable_for_tray()
       controls=controller.snapshot()
       update(controls=controls,attachments=controls['tasks'])
       release_generation=generation
       handoff=CodexTrayRelease(stop,lambda:tray_release.continuing(release_generation),approvals.pending_for)
      result=release_step(lambda:handoff.step(rpc,personal_sync,drained))
      if rpc and rpc.closed:codex_activity.disconnect()
     finally:
      tray_release.finish('codex',generation,result)
    elif tray_release.control_allowed():
     for previous in drained[:]:
      previous.project_writes.release_closed()
      if not previous.project_writes.tickets:drained.remove(previous)
     if not history_labels:history_labels=load_labels()
     if rpc is None or rpc.closed:
      if rpc:
       codex_activity.disconnect()
       if getattr(rpc,'draining',False):
        rpc.p.wait(timeout=3)
        if rpc.project_writes and rpc.project_writes.tickets:drained.append(rpc)
       rpc.close()
      client=device_client(cfg)
      rpc=RPC(approvals,journal=journal,coordination=client) if client else RPC(approvals,journal=journal)
      rpc.report_completion=codex_completed
      rpc.personal_session=lambda tid: personal_sync.snapshot()['sessions'].get(tid) if personal_sync else None
      rpc.personal_flow=lambda: personal_sync
      rpc.control_allowed=tray_release.control_allowed
     writes=getattr(rpc,'project_writes',None)
     if writes:
      try:flush_closes(close_queue(ROOT),writes.client)
      except (OSError,sqlite3.Error):pass
      if rpc.reconcile_writes():continue
     controller.tick(rpc)
     controls=controller.snapshot();update(controls=controls,attachments=controls['tasks'])
     if rpc.closed:
      update(connection='disconnected',serviceStatus='正在重连 Codex')
      continue
     managed_jobs,managed_status=managed.sync(rpc,cfg.get('managedTasks',[])) if tray_release.managed_allowed() else ([],[])
     update(managedTasks=managed_status)
     automatic_tasks=controller.automatic_tasks(
      rpc,cfg.get('tasks',[])+managed_jobs+controller.scheduled_tasks(rpc),
      explicit_only=not tray_release.managed_allowed())
     collect(rpc,{**cfg,'tasks':automatic_tasks},scheduler)
    approvals.expire()
    policy=ScopedPolicy(workspace,cfg.get('autoApprovalRules',[]))
    for token,value in list(approvals.items.items()):
     if value['decision'] is None and getattr(value['rpc'],'client_type','codex')=='codex':
      if tray_release.control_allowed() and value['rpc'] is rpc and policy.allows(rpc,value['request']):approvals.decide(token,True)
      else:
       details=value['request'].get('params',{})
       operation=str(details.get('command') or details.get('reason') or value['request'].get('method','权限请求'))
       update(windowsNotification=native.notify('approval:'+token,'Codex：需要你审批',' '.join(operation.split())[:160]+' | 请在桌面软件中核对详情后批准或拒绝'))
    for result in snapshot().get('schedule',[]):
     if result.startswith(('backoff:','blocked:goal')):
      native.notify(result+':'+str(int(time.time()//3600)),'Codex task waiting',result)
    webhook=os.environ.get('DINGTALK_WEBHOOK');secret=os.environ.get('DINGTALK_SIGNING_SECRET')
    if webhook and secret:
     if channel is None:channel=DingTalkWebhook(webhook,secret)
     update(notification='configured')
     for a in approvals.public():
      if a['decision'] is None:
       result=notifications.deliver(channel,a['token'],'需要人工审批','请在桌面软件中查看具体操作。当前通知不接受文字回复批准。')
       update(notification=result)
    failures=0
   except Exception:
    failures+=1;update(serviceStatus='retrying; details withheld',updated=int(time.time()))
    native.notify('failure:'+str(int(time.time()//3600)),'Codex monitor retrying','Connection unavailable; no replacement research thread created.')
   tray_release.wait('codex',stop,3 if not tray_release.control_allowed() else min(300,3*2**min(failures,6)))
 finally:
  if rpc:
   if getattr(rpc,'project_writes',None) and not rpc.closed:rpc.reconcile_writes(shutdown=True)
   rpc.close()
  codex_activity.disconnect()
  if controller:controller.db.close()
  scheduler.db.close();notifications.db.close();managed.db.close()


def watch_codex(stop):
 """Observe completed turns even when the controller's RPC is offline."""
 home=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
 started=time.time()
 rollouts=RolloutWatch(home,started)
 native=WindowsNotifications(str(ROOT/'windows-notifications.sqlite3'))
 daemon=None
 store=None
 next_probe=0

 def alert(item):
  name=item.get('title') or history_labels.get(item['threadId'],{}).get('title') or 'Codex 会话'
  if item.get('deviceName'):name=item['deviceName']+' · '+name
  key=f"codex:turn-complete:{item['threadId']}:{item['turnId']}"
  try:native.notify(key,'Codex 对话已完成',name)
  except sqlite3.Error:return

 def local(item):
  workspace_codex.complete(item)
  alert(item)

 try:
  while not stop.is_set():
   workspace_status='local'
   try:
    client=device_client(config())
    identity=(client.url,client.device,client.token) if client and client.token else None
    workspace_codex.set_identity(identity)
    store=RemoteRecords(client) if identity else None
   except (OSError,ValueError,KeyError,TypeError):
    workspace_codex.set_identity(None)
    store=None
    workspace_status='retrying'
   while not stop.is_set():
    try:item=codex_completions.get_nowait()
    except queue.Empty:break
    local(item)
   for item in rollouts.scan(stop):local(item)
   now=time.time()
   try:
    if daemon and daemon.closed:
     daemon.close();daemon=None
    if daemon is None and now>=next_probe and (home/'app-server-control').is_dir():
     next_probe=now+8
     daemon=SharedCodex(approvals,codex_completed,shared_approval_coordination)
    if daemon:daemon.poll_approvals(stop)
   except (OSError,ValueError,RPCError,queue.Empty):
    if daemon:daemon.close();daemon=None
    next_probe=now+8
   update(codexApprovalWatch='shared' if daemon and not daemon.closed else 'own_connection')
   try:
    if store:
     for row in workspace_codex.sync(store):
      stamp=row.get('time')
      if not recent(stamp,now,RETENTION) or stamp<started:continue
      name=row.get('title')
      item=completion(row.get('threadId'),row.get('turnId'),stamp,name if isinstance(name,str) else '')
      if item:alert({**item,'deviceName':row['deviceName']})
     workspace_status='connected'
   except (OSError,ValueError,KeyError,TypeError):
    workspace_status='retrying'
   update(codexWorkspaceWatch=workspace_status)
   stop.wait(2)
 finally:
  if daemon:daemon.close()
  if store:
   try:workspace_codex.disconnect(store)
   except (OSError,ValueError):pass
  workspace_codex.set_identity(None)
  native.db.close()

def poll_claude(stop):
 """An independent worker keeps Codex outages from blocking Claude monitoring."""
 global claude_controller,claude_rpc,claude_journal
 from claude_rpc import ClaudeCodeRPC
 claude_controller=TaskControl(ROOT/'claude-task-control.sqlite3')
 claude_controller.seed(config().get('claudeAutoAttachThreads',[]),managed=False)
 claude_controller.control_allowed=tray_release.control_allowed
 release_generation=None
 claude_journal=ReplyLog(str(ROOT/'claude-replies.sqlite3'))
 native=WindowsNotifications(str(ROOT/'claude-windows-notifications.sqlite3'))
 try:
  while not stop.is_set():
   try:
    generation=tray_release.begin('claude')
    schedule=[]
    with state_lock:threads=state['claude']['threads']
    if generation is not None:
     result=('waiting',0,'本机 Claude Code 暂未完成交还，后台将重试。')
     try:
      if generation != release_generation:
       claude_controller.disable_for_tray()
       release_generation=generation
      if claude_rpc is None or claude_rpc.drain_for_tray():
       result=('released',0,'本机 Claude Code 控制权已释放。')
      else:
       result=('waiting',len(claude_rpc.owned_threads),'等待本机 Claude Code 轮次和审批结束。')
     finally:
      tray_release.finish('claude',generation,result)
    elif tray_release.control_allowed():
     if claude_rpc is None or claude_rpc.closed:
      claude_rpc=ClaudeCodeRPC(approvals=approvals,journal=claude_journal,drafts_path=ROOT/'claude-drafts.json')
      claude_rpc.control_allowed=tray_release.control_allowed
     claude_controller.tick(claude_rpc)
     schedule=claude_controller.tick_auto_progress(claude_rpc)
     threads=claude_rpc.call('thread/list',{'limit':50}).get('data',[])
    controls=claude_controller.snapshot()
    names=controls.get('names') or {}
    for item in threads:
     if item.get('id') in names:item['title']=names[item['id']]
    approvals.expire()
    for a in approvals.public():
     if a.get('client')=='claude' and a['decision'] is None:
      native.notify('approval:'+a['token'],'Claude Code：需要你审批',a['details'].get('reason','请在监控中查看工具申请。'))
    claude_journal.notify_replies(native,'Claude Code 回复')
    update(claude={'connection':'connected','updated':int(time.time()),'threads':threads,'controls':controls,'attachments':controls['tasks'],
                   'quota':None,'quotaStatus':'unknown: Claude Code CLI does not expose official quota','schedule':schedule,
                   'capabilities':{'automaticContinue':True},'serviceStatus':'','notification':'local'})
   except Exception as error:
    update(claude={'connection':'disconnected','updated':int(time.time()),'threads':[],
                   'controls':claude_controller.snapshot(),'quota':None,'quotaStatus':'unknown','schedule':[],
                   'serviceStatus':str(error)[:300] if isinstance(error,FileNotFoundError) else 'Claude Code 连接待检查，请查看本机 CLI。'})
   tray_release.wait('claude',stop,3)
 finally:
  if claude_rpc:claude_rpc.close()
  claude_controller.db.close();claude_journal.db.close();native.db.close()

class ServiceWorkers:
 def __init__(self,threads,service_lock=None):self.threads=threads;self.service_lock=service_lock
 def join(self,timeout=None):
  deadline=None if timeout is None else time.monotonic()+timeout
  for thread in self.threads:thread.join(None if deadline is None else max(0,deadline-time.monotonic()))
  if not self.is_alive() and self.service_lock:self.service_lock.close()
 def is_alive(self):return any(thread.is_alive() for thread in self.threads)

def start_background_service(stop=None, embed_web=None):
 """Start independent Codex and Claude workers inside the desktop process."""
 from service_lock import ServiceLock
 stop = stop or threading.Event()
 if embed_web is None:embed_web=os.environ.get('MONITOR_EMBED_WEB')=='1'
 web_worker=None
 if embed_web:
  from web_server import embedded_web_worker
  web_worker=embedded_web_worker(stop)
 service_lock=ServiceLock(ROOT/'monitor.lockfile')
 worker=threading.Thread(target=poll,args=(stop,),daemon=True,name='monitor-worker')
 claude_worker=threading.Thread(target=poll_claude,args=(stop,),daemon=True,name='claude-monitor-worker')
 workers=[worker,claude_worker,threading.Thread(target=watch_codex,args=(stop,),daemon=True,name='codex-watch')]
 from project_library import ProjectLibrary
 library=ProjectLibrary(ROOT,Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex'))
 workers.append(threading.Thread(target=library.run,args=(stop,update),daemon=True,name='project-library'))
 if web_worker:workers.append(web_worker)
 try:
  settings=config()
  transfer=settings.get('handoffSync')
 except (ValueError,OSError,AttributeError):
  settings={};transfer=None
 if transfer is not None:
  from handoff_sync import run as run_handoff_sync
  def report_handoff(status):update(handoffSyncStatus=status)
  workers.append(threading.Thread(target=run_handoff_sync,args=(ROOT,transfer,stop,report_handoff,settings.get('coordination')),
                                  daemon=True,name='handoff-sync'))
 for thread in workers:thread.start()
 if os.environ.get('DINGTALK_CLIENT_ID') and os.environ.get('DINGTALK_CLIENT_SECRET'):
  from dingtalk_commands import CommandRouter,run_stream
  cfg=config()
  router=CommandRouter(approvals,snapshot,cfg.get('streamIdentities',[]))
  def stream_worker():
   update(streamStatus='starting; awaiting callback')
   try:run_stream(router,os.environ['DINGTALK_CLIENT_ID'],os.environ['DINGTALK_CLIENT_SECRET'],lambda:update(streamStatus='callback_received'))
   except Exception:update(streamStatus='unavailable; credentials or SDK need local verification')
  threading.Thread(target=stream_worker,daemon=True,name='dingtalk-stream').start()
 group=ServiceWorkers(workers,service_lock)
 global personal_sync
 personal_sync=None
 try:
  personal=personal_settings(settings) if isinstance(settings,dict) else None
  if isinstance(personal,dict):
   enable_personal_sync(personal,stop,group)
 except (ValueError,OSError,KeyError,TypeError):
  update(personalSyncError='个人设备同步配置无效')
 return stop,group
