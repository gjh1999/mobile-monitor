import json,tempfile,unittest,threading
from pathlib import Path
from task_control import TaskControl
from rpc_client import RPCError
from rpc_errors import RPCNotSent
TARGET='01a1cad9-8028-7771-bf25-2115a5c73e1f'
OTHER='01a1cad9-8028-7771-bf25-2115a5c73e20'


class Mock:
 def __init__(self):self.calls=[];self.owned_threads=set();self.writer=False;self.active=False;self.goal='active';self.uncertain=False
 def call(self,method,params):
  self.calls.append((method,params))
  if method=='thread/resume' and self.writer:raise RPCError(detail='already has an active writer')
  if method in ['thread/resume','thread/read']:return {'thread':{'id':params.get('threadId', TARGET),'status':{'type':'active' if self.active else 'idle'}}}
  if method=='thread/goal/get':return {'goal':{'status':self.goal}}
  if method=='thread/start':return {'thread':{'id':'00000000-0000-0000-0000-000000000123','status':{'type':'idle'}}}
  if method=='thread/turns/list':return {'data':[{'id':'turn-1','status':'inProgress'}] if self.active else []}
  if method in ['turn/start','turn/steer'] and self.uncertain:raise TimeoutError()
  return {}


class Tests(unittest.TestCase):
 def make(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  control=TaskControl(Path(tmp.name)/'tasks.db');self.addCleanup(control.db.close)
  return control
 def test_automatic_attach_and_detach_survives_default_seed(self):
  c=self.make();r=Mock();c.seed([TARGET]);c.tick(r)
  self.assertTrue(c.snapshot()['tasks'][0]['canControl'])
  c.submit(TARGET,'detach',request_id='detach1');c.tick(r);c.seed([TARGET]);c.tick(r)
  self.assertFalse(c.snapshot()['tasks']);self.assertFalse(r.owned_threads)
 def test_writer_lock_is_observer_not_fake_control(self):
  c=self.make();r=Mock();r.writer=True;c.seed([TARGET]);c.tick(r)
  self.assertEqual(c.snapshot()['tasks'][0]['mode'],'observe');self.assertFalse(r.owned_threads)
  c.submit(TARGET,'message','do work','send1');c.tick(r)
  self.assertEqual(c.snapshot()['operations'][0]['status'],'failed')
  self.assertNotIn('turn/start',[m for m,p in r.calls])
 def test_latest_detach_supersedes_queued_and_inflight_connection(self):
  for action in ('attach','focus'):
   for inflight in (False,True):
    with self.subTest(action=action,inflight=inflight):
     control=self.make();rpc=Mock()
     control.seed([OTHER]);control.tick(rpc)
     original=rpc.call
     def call(method,params):
      if method=='thread/resume' and params['threadId']==TARGET:
       control.submit(TARGET,'detach')
      return original(method,params)
     key=control.submit(TARGET,action)
     if inflight:rpc.call=call
     else:control.submit(TARGET,'detach')
     control.tick(rpc)
     self.assertEqual({t['threadId'] for t in control.snapshot()['tasks']},{OTHER})
     self.assertNotIn(TARGET,rpc.owned_threads)
     self.assertEqual(next(o['status'] for o in control.snapshot()['operations'] if o['id']==key),'cancelled')
     rpc.call=original
     control.submit(TARGET,'attach')
     control.tick(rpc)
     self.assertIn(TARGET,rpc.owned_threads)

 def test_late_stop_blocks_codex_start_steer_and_resume(self):
  for action,active in [('message',False),('message',True),('resume',False)]:
   for stop in ('pause','detach'):
    with self.subTest(action=action,active=active,stop=stop):
     control=self.make();rpc=Mock();rpc.active=active
     control.seed([TARGET]);control.tick(rpc)
     original=rpc.call
     def call(method,params):
      if method in ('turn/start','turn/steer'):
       control.submit(TARGET,stop)
       if not params['_sendGuard']():
        raise RPCError(detail='stopped')
      return original(method,params)
     rpc.call=call
     key=control.submit(TARGET,action,'continue')
     control.tick(rpc)
     self.assertEqual(next(o['status'] for o in control.snapshot()['operations'] if o['id']==key),'failed')
     self.assertFalse(any(m in ('turn/start','turn/steer') for m,_ in rpc.calls))

 def test_running_turn_remains_readable_after_focus_switch(self):
  from reply_log import ReplyLog
  control=self.make();rpc=Mock();rpc.active=True
  rpc.journal=ReplyLog(':memory:');self.addCleanup(rpc.journal.db.close)
  control.seed([TARGET]);control.tick(rpc)
  control.submit(OTHER,'focus');control.tick(rpc)
  self.assertEqual(rpc.owned_threads,{OTHER})
  def record(method,**params):
   return rpc.journal.record({'method':method,'params':{'threadId':TARGET,**params}},rpc.owned_threads)
  item={'type':'agentMessage','id':'a','text':'final reply'}
  self.assertTrue(record('item/completed',turnId='turn-1',item=item))
  self.assertFalse(record('item/completed',turnId='another',item=item))
  record('turn/completed',turn={'id':'turn-1','status':'completed'})
  rpc.journal.track_turn(TARGET,'turn-1')
  self.assertFalse(record('item/completed',turnId='turn-1',item={**item,'id':'late'}))
  record('turn/started',turn={'id':'unowned'})
  self.assertFalse(record('item/completed',turnId='unowned',item=item))
  self.assertEqual(rpc.journal.db.execute('SELECT text FROM replies').fetchall(),[('final reply',)])
 def test_codex_choices_and_settings_are_scoped_to_the_owned_thread(self):
  control=self.make()
  rpc=Mock()
  control.seed([TARGET],managed=False)
  control.tick(rpc)
  original_call=rpc.call

  def call(method,params,timeout=20):
   if method=='model/list':
    return {'data':[{'model':'real-model','displayName':'Real model','defaultReasoningEffort':'high',
                     'supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]}
   if method=='permissionProfile/list':
    return {'data':[{'id':':workspace','allowed':True},{'id':':danger-full-access','allowed':False}]}
   if method=='thread/settings/update':
    self.assertTrue(params['_sendGuard']())
   return original_call(method,params)

  rpc.call=call
  for field,value in (('model','real-model'),('permissions',':workspace')):
   control.submit(TARGET,'codex_settings',json.dumps({'field':field,'value':value}))
   control.tick(rpc)
   self.assertEqual(control.snapshot()['operations'][0]['status'],'succeeded')
  settings=control.snapshot()['tasks'][0]['codexSettings']
  self.assertEqual(settings,{'model':'real-model','effort':'high','permissions':':workspace'})
  writes=[{key:value for key,value in params.items() if key!='_sendGuard'}
          for method,params in rpc.calls if method=='thread/settings/update']
  self.assertEqual(writes,[{'threadId':TARGET,'model':'real-model','effort':'high'},
                          {'threadId':TARGET,'permissions':':workspace'}])
  control.submit(TARGET,'codex_settings',json.dumps({'field':'permissions','value':':danger-full-access'}))
  control.tick(rpc)
  self.assertEqual(control.snapshot()['operations'][0]['status'],'failed')
  with self.assertRaisesRegex(ValueError,'请先执舟'):
   control.codex_options(OTHER,'model')
  self.assertEqual(sum(method=='thread/settings/update' for method,_ in rpc.calls),2)
 def test_focus_replaces_only_after_success_and_preserves_stop_intent(self):
  c=self.make();r=Mock();c.seed([TARGET]);c.tick(r)
  r.writer=True
  c.submit(OTHER,'focus',request_id='locked');c.tick(r)
  self.assertEqual(r.owned_threads,{TARGET})
  self.assertEqual(c.snapshot()['operations'][0]['status'],'failed')
  r.writer=False
  c.submit(TARGET,'focus',request_id='earlier')
  c.submit(OTHER,'focus',request_id='latest')
  c.submit(TARGET,'message','keep the old draft',request_id='old-message')
  c.tick(r)
  snapshot=c.snapshot()
  self.assertEqual(r.owned_threads,{OTHER})
  self.assertEqual([(t['threadId'],t['managed']) for t in snapshot['tasks']],[(OTHER,False)])
  self.assertEqual({o['id']:o['status'] for o in snapshot['operations'] if o['id']!='locked'},
                   {'earlier':'cancelled','latest':'succeeded','old-message':'cancelled'})
  self.assertFalse(any(method in ('turn/start','turn/steer','turn/interrupt') for method,_ in r.calls))
  c.submit(TARGET,'focus',request_id='back');c.tick(r)
  self.assertEqual(r.owned_threads,{TARGET})
  self.assertTrue(c.snapshot()['tasks'][0]['managed'])
 def test_pausing_interrupts_exact_active_turn_and_prevents_messages(self):
  c=self.make();r=Mock();r.active=True;c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'pause',request_id='pause1');c.tick(r)
  self.assertIn(('turn/interrupt',{'threadId':TARGET,'turnId':'turn-1'}),r.calls)
  c.submit(TARGET,'message','work','send1');c.tick(r)
  self.assertEqual(c.snapshot()['operations'][0]['status'],'failed')
 def test_message_idempotency_and_active_turn_steering(self):
  c=self.make();r=Mock();r.active=True;c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'message','check progress','same1');c.submit(TARGET,'message','check progress','same1');c.tick(r)
  sends=[p for m,p in r.calls if m=='turn/steer'];self.assertEqual(len(sends),1);self.assertEqual(sends[0]['expectedTurnId'],'turn-1')
  with self.assertRaises(ValueError):c.submit(TARGET,'message','different','same1')
 def test_uncertain_delivery_never_retries(self):
  c=self.make();r=Mock();r.uncertain=True;c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'message','work','send1');c.tick(r);c.tick(r)
  self.assertEqual(c.snapshot()['operations'][0]['status'],'unknown')
  self.assertEqual(sum(m=='turn/start' for m,p in r.calls),1)

 def test_reconnect_dispatches_the_same_unsent_message_once(self):
  for state_probe, action, active in ((True, 'message', False), (False, 'message', True),
                                      (False, 'resume', False)):
   with self.subTest(state_probe=state_probe, action=action, active=active):
    control=self.make();rpc=Mock();rpc.active=active
    control.seed([TARGET]);control.tick(rpc)
    if action=='resume':
     control.submit(TARGET,'pause');control.tick(rpc)
    before=rpc.call
    turn_method='turn/steer' if active else 'turn/start'

    def disconnect(method,params):
     if method==('thread/turns/list' if state_probe else turn_method):
      rpc.closed=True
      if state_probe:
       raise ConnectionError('lost the read-only response')
      raise RPCNotSent('closed before writing')
     return before(method,params)

    rpc.call=disconnect
    key=control.submit(TARGET,action,'继续',request_id='continue-after-reconnect')
    control.tick(rpc);control.tick(rpc)
    operation=next(o for o in control.snapshot()['operations'] if o['id']==key)
    self.assertEqual(operation['status'],'queued')
    self.assertIn('指令尚未发送',operation['message'])
    self.assertNotIn(turn_method,[method for method,_ in rpc.calls])
    replacement=Mock();replacement.active=active
    control.tick(replacement);control.tick(replacement)
    self.assertEqual(next(o['status'] for o in control.snapshot()['operations'] if o['id']==key),'succeeded')
    sends=[params for method,params in replacement.calls if method==turn_method]
    self.assertEqual([send['clientUserMessageId'] for send in sends],[key])
    if action=='message':
     self.assertEqual(sends[0]['input'],[{'type':'text','text':'继续'}])

 def test_loss_after_write_stays_unknown_across_reconnect(self):
  control=self.make();rpc=Mock();control.seed([TARGET]);control.tick(rpc)
  before=rpc.call

  def disconnect(method,params):
   if method=='turn/start':
    rpc.calls.append((method,params));rpc.closed=True
    raise ConnectionError('lost the response after writing')
   return before(method,params)

  rpc.call=disconnect
  key=control.submit(TARGET,'message','继续')
  control.tick(rpc)
  replacement=Mock();control.tick(replacement)
  self.assertEqual(control.snapshot()['operations'][0]['status'],'unknown')
  self.assertEqual([p['clientUserMessageId'] for m,p in rpc.calls if m=='turn/start'],[key])
  self.assertNotIn('turn/start',[method for method,_ in replacement.calls])

 def test_pause_cancels_a_message_queued_for_reconnection(self):
  control=self.make();rpc=Mock();control.seed([TARGET]);control.tick(rpc)
  before=rpc.call

  def disconnect(method,params):
   if method=='turn/start':
    rpc.closed=True
    raise RPCNotSent('closed before writing')
   return before(method,params)

  rpc.call=disconnect
  key=control.submit(TARGET,'message','继续')
  control.tick(rpc)
  control.submit(TARGET,'pause')
  replacement=Mock();control.tick(replacement)
  self.assertEqual(next(o['status'] for o in control.snapshot()['operations'] if o['id']==key),'cancelled')
  self.assertNotIn('turn/start',[method for method,_ in replacement.calls])
 def test_manual_resume_rechecks_usage_limit_without_resetting_goal(self):
  c=self.make();r=Mock();r.goal='usageLimited';c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'pause',request_id='pause1');c.tick(r)
  c.submit(TARGET,'resume',request_id='resume1');c.tick(r);c.tick(r)
  self.assertFalse(c.snapshot()['tasks'][0]['paused'])
  self.assertEqual(c.snapshot()['operations'][0]['status'],'succeeded')
  self.assertEqual(sum(m=='turn/start' for m,p in r.calls),1)
  self.assertEqual(r.goal,'usageLimited')
  self.assertNotIn('thread/goal/set',[m for m,p in r.calls])
 def test_manual_resume_keeps_pause_when_cli_still_rejects_usage(self):
  c=self.make();r=Mock();r.goal='usageLimited';c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'pause',request_id='pause-before-quota-check');c.tick(r)
  original=r.call
  def reject(method,params):
   if method=='turn/start':
    r.calls.append((method,params))
    raise RPCError(detail='Usage limit reached; try again after reset')
   return original(method,params)
  r.call=reject
  c.submit(TARGET,'resume',request_id='quota-still-unavailable');c.tick(r);c.tick(r)
  self.assertTrue(c.snapshot()['tasks'][0]['paused'])
  operation=c.snapshot()['operations'][0]
  self.assertEqual(operation['status'],'failed')
  self.assertIn('Usage limit reached',operation['message'])
  self.assertEqual(sum(m=='turn/start' for m,p in r.calls),1)
  self.assertNotIn('thread/goal/set',[m for m,p in r.calls])
 def test_manual_resume_does_not_reset_completed_or_budget_limited_goal(self):
  for goal in ('complete','budgetLimited'):
   with self.subTest(goal=goal):
    c=self.make();r=Mock();r.goal=goal;c.seed([TARGET]);c.tick(r)
    c.submit(TARGET,'pause',request_id='pause-protected');c.tick(r)
    c.submit(TARGET,'resume',request_id='resume-protected');c.tick(r)
    self.assertTrue(c.snapshot()['tasks'][0]['paused'])
    self.assertEqual(c.snapshot()['operations'][0]['status'],'failed')
    self.assertNotIn('turn/start',[m for m,p in r.calls])
    self.assertNotIn('thread/goal/set',[m for m,p in r.calls])
 def test_reattach_running_codex_preserves_pause_until_explicit_continue(self):
  c=self.make();r=Mock();c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'pause',request_id='old-local-pause');c.tick(r)
  r.active=True
  c.submit(TARGET,'attach',request_id='reattach-running');c.tick(r)
  c.submit(TARGET,'unmanage',request_id='manual-only');c.tick(r)
  task=c.snapshot()['tasks'][0]
  self.assertTrue(task['paused']);self.assertTrue(task['canControl'])
  self.assertEqual(task['threadStatus']['type'],'active')
  self.assertFalse(c.scheduled_tasks(r))
  r.calls.clear()
  c.submit(TARGET,'resume',request_id='resume-already-running');c.tick(r)
  task=c.snapshot()['tasks'][0]
  self.assertFalse(task['paused']);self.assertFalse(task['managed'])
  self.assertEqual(c.snapshot()['operations'][0]['status'],'succeeded')
  self.assertFalse([m for m,p in r.calls if m in ('turn/start','turn/steer','thread/goal/set')])
  c.tick(r)
  c.submit(TARGET,'message','补充当前任务方向','message-after-unpause');c.tick(r)
  writes=[(m,p) for m,p in r.calls if m in ('turn/start','turn/steer')]
  self.assertEqual(len(writes),1);self.assertEqual(writes[0][0],'turn/steer')
  self.assertEqual(writes[0][1]['threadId'],TARGET)
  self.assertEqual(writes[0][1]['expectedTurnId'],'turn-1')
  self.assertEqual(writes[0][1]['input'][0]['text'],'补充当前任务方向')
 def test_continue_running_codex_does_not_override_a_newer_pause(self):
  c=self.make();r=Mock();r.active=True;c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'pause',request_id='original-pause');c.tick(r);r.calls.clear()
  c.submit(TARGET,'resume',request_id='continue-before-new-pause')
  c.submit(TARGET,'pause',request_id='newer-pause');c.tick(r)
  self.assertTrue(c.snapshot()['tasks'][0]['paused'])
  operation=next(op for op in c.snapshot()['operations'] if op['id']=='continue-before-new-pause')
  self.assertEqual(operation['status'],'cancelled')
  self.assertFalse([m for m,p in r.calls if m in ('turn/start','turn/steer','thread/goal/set')])
 def test_continue_running_codex_requires_ownership(self):
  c=self.make();r=Mock();r.active=True;r.writer=True;c.seed([TARGET]);c.tick(r)
  c.submit(TARGET,'pause',request_id='pause-observed');c.tick(r);r.calls.clear()
  c.submit(TARGET,'resume',request_id='continue-observed');c.tick(r)
  self.assertTrue(c.snapshot()['tasks'][0]['paused'])
  self.assertFalse(c.snapshot()['tasks'][0]['canControl'])
  self.assertEqual(c.snapshot()['operations'][0]['status'],'failed')
  self.assertFalse([m for m,p in r.calls if m in ('turn/start','turn/steer','thread/goal/set')])
 def test_attach_usage_limited_task_never_sends_model_request(self):
  c=self.make();r=Mock();r.goal='usageLimited'
  c.submit(TARGET,'attach',request_id='attach-with-old-limit');c.tick(r)
  self.assertEqual(c.snapshot()['operations'][0]['status'],'succeeded')
  self.assertTrue(c.snapshot()['tasks'][0]['canControl'])
  self.assertNotIn('turn/start',[m for m,p in r.calls])
  self.assertNotIn('thread/goal/set',[m for m,p in r.calls])
 def test_cancel_management_keeps_manual_messages_and_reconnect_defaults_to_managed(self):
  c=self.make();r=Mock();c.seed([TARGET]);c.tick(r)
  self.assertTrue(c.snapshot()['tasks'][0]['managed']);self.assertEqual(len(c.scheduled_tasks(r)),1)
  c.submit(TARGET,'unmanage',request_id='unmanage1');c.tick(r)
  self.assertFalse(c.snapshot()['tasks'][0]['managed']);self.assertFalse(c.scheduled_tasks(r))
  c.submit(TARGET,'message','manual still works','manual1');c.tick(r)
  self.assertEqual(c.snapshot()['operations'][0]['status'],'succeeded')
  c.submit(TARGET,'attach',request_id='reattach1');c.tick(r)
  self.assertTrue(c.snapshot()['tasks'][0]['managed'])
 def test_test_task_creation_does_not_start_model_or_touch_research(self):
  c=self.make();r=Mock();c.submit(TARGET,'create_test',request_id='create-test1');c.tick(r)
  task=c.snapshot()['tasks'][0]
  self.assertEqual(task['title'],'桌面功能测试');self.assertTrue(task['canControl']);self.assertTrue(task['managed'])
  self.assertNotIn('turn/start',[m for m,p in r.calls]);self.assertNotIn('thread/goal/set',[m for m,p in r.calls])
 def test_rename_persists_without_attaching_or_calling_cli(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'control.db';c=TaskControl(path);r=Mock()
   c.submit(TARGET,'rename','  我 的 项目  ','rename1');c.tick(r)
   self.assertEqual(c.snapshot()['names'][TARGET],'我 的 项目');self.assertFalse(r.calls)
   c.db.close();c=TaskControl(path)
   try:
    c.seed([TARGET]);c.tick(r)
    self.assertEqual(c.snapshot()['tasks'][0]['title'],'我 的 项目')
    c.submit(TARGET,'detach',request_id='detach');c.tick(r)
    self.assertEqual(c.snapshot()['names'][TARGET],'我 的 项目')
   finally:c.db.close()
 def test_rename_validation_and_client_isolation(self):
  c=self.make();other=self.make();r=Mock()
  for title in ('','  ','x'*81,'one\ntwo','one\ttwo'):
   with self.assertRaises(ValueError):c.submit(TARGET,'rename',title)
  c.submit(TARGET,'rename','研究计划','same');c.submit(TARGET,'rename','研究计划','same');c.tick(r)
  self.assertEqual(c.snapshot()['names'][TARGET],'研究计划');self.assertFalse(other.snapshot()['names'])
  with self.assertRaises(ValueError):c.submit(TARGET,'rename','different','same')
 def test_pause_has_priority_and_cancels_unsent_messages(self):
  c=self.make();r=Mock();r.active=True;c.seed([TARGET]);c.tick(r);r.calls.clear()
  for i in range(12):c.submit(TARGET,'message','pending','pending'+str(i))
  c.submit(TARGET,'pause',request_id='stop');self.assertTrue(c.snapshot()['tasks'][0]['paused'])
  c.tick(r)
  self.assertEqual([m for m,p in r.calls if m.startswith('turn/')],['turn/interrupt'])
  self.assertEqual(sum(op['status']=='cancelled' for op in c.snapshot()['operations']),12)
 def test_submit_and_snapshot_do_not_wait_on_slow_rpc(self):
  c=self.make();r=Mock();c.seed([TARGET]);c.tick(r)
  entered=threading.Event();release=threading.Event();original=r.call
  def slow(method,params):
   if method=='turn/start':entered.set();release.wait(3)
   return original(method,params)
  r.call=slow;c.submit(TARGET,'message','work','slow')
  worker=threading.Thread(target=c.tick,args=(r,));worker.start()
  try:
   self.assertTrue(entered.wait(2))
   c.submit(TARGET,'pause',request_id='stop-while-busy')
   self.assertTrue(c.snapshot()['tasks'][0]['paused'])
  finally:release.set();worker.join(5)
 def test_pause_submits_interrupt_without_waiting_for_background_tick(self):
  c=self.make();r=Mock();r.client_type='claude';r.control_available=lambda tid:True
  c.seed([TARGET]);c.tick(r);r.calls.clear()
  started=threading.Event();release=threading.Event()
  def call(method,params):
   r.calls.append((method,params))
   if method=='turn/interrupt':started.set();release.wait(3);return {}
   return Mock.call(r,method,params)
  r.call=call;c.connection=r;c.runtime[TARGET]={'threadId':TARGET,'canControl':True,'mode':'control'};r.owned_threads={TARGET}
  c.submit(TARGET,'pause',request_id='async-pause')
  try:
   self.assertTrue(started.wait(1))
   self.assertTrue(c.snapshot()['tasks'][0]['paused'])
  finally:release.set()
 def test_controller_snapshot_is_ready_only_after_database_initialization(self):
  c=self.make()
  c.db.execute("INSERT INTO operations VALUES('old-op',?,'message','','failed','请先接入这个任务。',0)",(TARGET,));c.db.commit()
  self.assertTrue(c.snapshot()['ready'])
  self.assertEqual(c.snapshot()['operations'][0]['status'],'failed')
  self.assertEqual(c.snapshot()['operations'][0]['stamp'],0)
 def test_desktop_command_reaches_the_selected_original_task(self):
  import monitor
  from unittest.mock import patch
  c=self.make();r=Mock();c.seed([TARGET]);c.tick(r)
  with patch.object(monitor,'controller',c):
   operation=monitor.get_controller().submit(TARGET,'message','Check progress','desktop-test')
   self.assertEqual(operation,'desktop-test')
   c.tick(r)
  sent=next(p for m,p in r.calls if m=='turn/start')
  self.assertEqual(sent['threadId'],TARGET);self.assertEqual(sent['input'][0]['text'],'Check progress')
