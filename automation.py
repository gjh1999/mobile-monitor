"""Connection-bound approvals and durable wake reservations."""
import copy, hashlib, json, queue, secrets, sqlite3, threading, time
from quota_recovery import QuotaRecovery, quota_state
from coordination_client import ControlDenied
from rpc_errors import RPCError
APPROVAL_METHODS = {'item/commandExecution/requestApproval','item/fileChange/requestApproval','item/permissions/requestApproval'}
APPROVAL_DETAILS = ('command','changes','permissions','networkApprovalContext')

def can_allow(details):
 return any(details.get(key) for key in APPROVAL_DETAILS)

class Approvals:
 def __init__(self,clock=time.time):self.items={};self.lock=threading.RLock();self.clock=clock
 def add(self,rpc,request,ttl=300):
  if request.get('method') not in APPROVAL_METHODS or 'id' not in request:raise ValueError('Unsupported approval')
  with self.lock:
   for k,v in self.items.items():
    if v['rpc'] is rpc and v['request']['id']==request['id']:return k
   token=secrets.token_urlsafe(24)
   self.items[token]=dict(rpc=rpc,request=copy.deepcopy(request),expires=self.clock()+ttl,decision=None)
   return token
 def public(self):
  with self.lock:return [dict(token=k,client=getattr(v['rpc'],'client_type','codex'),method=v['request']['method'],details=copy.deepcopy(v['request'].get('params',{})),expires=v['expires'],decision=v['decision']) for k,v in self.items.items()]
 def pending(self,client=None):
  now=self.clock()
  return [{**item,'canAllow':can_allow(item['details'])} for item in self.public()
          if item['decision'] is None and item['expires']>now and (client is None or item['client']==client)]
 def refresh_live(self,rpc,thread,ttl=300):
  with self.lock:
   for value in self.items.values():
    if value['rpc'] is rpc and value['decision'] is None and value['request'].get('params',{}).get('threadId')==thread:
     value['expires']=self.clock()+ttl
 def resolve(self,rpc,request_id):
  with self.lock:
   for v in self.items.values():
    if v['rpc'] is rpc and v['request']['id']==request_id and v['decision'] is None:v['decision']='resolved'
 def disconnect(self,rpc):
  with self.lock:
   for v in self.items.values():
    if v['rpc'] is rpc and v['decision'] is None:v['decision']='disconnected'
 def dismiss_thread(self,rpc,thread):
  with self.lock:
   for v in self.items.values():
    if v['rpc'] is rpc and v['decision'] is None and v['request'].get('params',{}).get('threadId')==thread:
     v['decision']='resolved'
 def decide(self,token,allow):
  guard=None
  with self.lock:
   v=self.items[token]
   if v['decision'] is not None:return v['decision']
   req=v['request'];p=req.get('params',{})
   provider=getattr(v['rpc'],'approval_guard',None) if allow and self.clock()<v['expires'] else None
  if provider is not None:
   try:guard=provider(p.get('threadId'))
   except ControlDenied:return 'control_lost'
  with self.lock:
   if v['decision'] is not None:return v['decision']
   allow=allow and self.clock()<v['expires']
   if allow and not can_allow(p):raise ValueError('Concrete approval details unavailable')
   result=({'permissions':p.get('permissions',{}) if allow else {},'scope':'turn'} if req['method']=='item/permissions/requestApproval' else {'decision':'accept' if allow else 'decline'})
   v['decision']='responding'
   try:
    if not allow or guard is None:v['rpc'].send({'id':req['id'],'result':result})
    else:v['rpc'].send({'id':req['id'],'result':result},guard=guard)
   except ControlDenied:
    v['decision']=None
    return 'control_lost'
   except Exception:v['decision']='unknown_delivery'
   else:v['decision']='allowed' if allow else 'denied'
   return v['decision']
 def decide_pending(self,token,allow,client=None):
  with self.lock:
   value=self.items.get(token)
   if value is None or client is not None and getattr(value['rpc'],'client_type','codex')!=client:return 'not_found'
   if value['decision'] is not None:return value['decision']
   if value['expires']<=self.clock():return 'expired'
  try:return self.decide(token,allow)
  except ValueError:return 'details_unavailable'
  except (OSError,RPCError,queue.Empty):return 'unavailable'
 def expire(self):
  with self.lock:
   for k,v in self.items.items():
    if v['decision'] is None and self.clock()>=v['expires']:
     if getattr(v['rpc'],'monitor_only',False):v['decision']='expired'
     else:self.decide(k,False)
 def pending_for(self,thread_id):return any(v['decision'] is None and v['details'].get('threadId')==thread_id for v in self.public())
 def pending_on(self,rpc):
  with self.lock:return any(v['rpc'] is rpc and v['decision'] is None for v in self.items.values())
class Scheduler:
 def __init__(self,path=':memory:',approvals=None):
  self.lock=threading.Lock();self.approvals=approvals;self.db=sqlite3.connect(path,check_same_thread=False);self.last_results=[];self.last_check=None
  self.db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, next REAL, failures INTEGER)')
  self.db.execute('CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY, thread TEXT, state TEXT)');self.db.commit()
  self.quota_recovery=QuotaRecovery(self.db)
 def tick(self,rpc,tasks,now=None,quota=None):
  now=time.time() if now is None else now
  if not self.lock.acquire(False):return []
  out=[]
  try:
   # A local manual pause/detach/unmanage removes its automatic recovery intent.
   # Waiting is separate from the ordinary hourly task timer.
   self.quota_recovery.reconcile(tasks)
   for t in tasks:
    key=t['threadId'];interval=max(60,t.get('interval',3600));row=self.db.execute('SELECT next,failures FROM tasks WHERE id=?',(key,)).fetchone()
    waiting=self.quota_recovery.get(key)
    if t.get('completed'):continue
    if waiting and waiting['state'] in ('unknown','changed','account_changed'):
     out.append('blocked:quota_'+waiting['state']);continue
    if waiting and waiting['due']>now:
     out.append('blocked:quota');continue
    quota_check=quota_state(quota)['blocked'] or t.get('_goalStatus')=='usageLimited'
    if not waiting and not quota_check and row and now<row[0]:continue
    failures=row[1] if row else 0
    self.db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?)',(key,now+interval,failures));self.db.commit()
    try:
     thread=rpc.call('thread/read',{'threadId':key,'includeTurns':False})['thread'];status=thread.get('status',{}).get('type','unknown')
     if status not in ('idle','systemError') and not ((waiting or quota_check) and status=='active'):out.append('blocked:'+status);continue
     if not t.get('enabled') or not t.get('exclusiveOwner'):out.append('dry-run:'+key);continue
     if key not in getattr(rpc,'owned_threads',set()):out.append('blocked:unowned');continue
     if t.get('_sendGuard') is not None and not t['_sendGuard']():out.append('blocked:paused');continue
     goal=rpc.call('thread/goal/get',{'threadId':key}).get('goal')
     if waiting or (goal or {}).get('status')=='usageLimited' or ((goal or {}).get('status')=='active' and quota_state(quota)['blocked']):
      result=self.quota_recovery.handle(rpc,t,thread,goal,quota,now,self.approvals)
      if result in ('sent:quota','recovered:quota'):
       self.db.execute('UPDATE tasks SET next=?,failures=0 WHERE id=?',(now+interval,key))
       revision=thread.get('updatedAt')
       if revision is not None:
        stamp=hashlib.sha256(json.dumps([key,revision]).encode()).hexdigest()
        self.db.execute("INSERT OR IGNORE INTO reservations VALUES(?,?,'sent')",(stamp,key))
       self.db.commit()
      out.append(result);continue
     if status!='idle':out.append('blocked:'+status);continue
     if not goal or goal.get('status')!='active':out.append('blocked:goal');continue
     if self.approvals and self.approvals.pending_for(key):out.append('blocked:approval');continue
     revision=thread.get('updatedAt')
     if revision is None:out.append('blocked:revision');continue
     stamp=hashlib.sha256(json.dumps([key,revision]).encode()).hexdigest()
     if rpc.call('thread/read',{'threadId':key,'includeTurns':False})['thread'].get('status',{}).get('type')!='idle':out.append('blocked:changed');continue
     cur=self.db.execute('INSERT OR IGNORE INTO reservations VALUES(?,?,?)',(stamp,key,'reserved'));self.db.commit()
     if not cur.rowcount:out.append('blocked:duplicate');continue
     params={'threadId':key,'input':[{'type':'text','text':t['prompt']}]}
     if t.get('_sendGuard') is not None:params['_sendGuard']=t['_sendGuard']
     try:rpc.call('turn/start',params)
     except ControlDenied:
      self.db.execute("DELETE FROM reservations WHERE id=? AND state='reserved'",(stamp,))
      self.db.execute('UPDATE tasks SET next=? WHERE id=?',(now+5,key));self.db.commit()
      out.append('blocked:project_control');continue
     self.db.execute('UPDATE reservations SET state=? WHERE id=?',('sent',stamp));self.db.execute('UPDATE tasks SET failures=0 WHERE id=?',(key,));self.db.commit();out.append('sent:'+key)
    except Exception:
     pending_wait=self.quota_recovery.get(key)
     if pending_wait and pending_wait['state'] in ('waiting','checking'):
      self.quota_recovery.defer(key,'checking',now,pending_wait['resetsAt'])
     self.db.execute('UPDATE tasks SET next=?,failures=? WHERE id=?',(now+min(3600,interval*2**min(failures+1,5)),failures+1,key));self.db.commit();out.append('backoff:'+key)
   self.last_results=out;self.last_check=now;return out
  finally:self.lock.release()

