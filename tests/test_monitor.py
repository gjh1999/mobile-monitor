import copy, queue, tempfile, threading, unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import monitor
from automation import Approvals, Scheduler
from rpc_client import RPC, RPCError
from task_control import TaskControl
from rpc_errors import RPCNotSent


class FakeRPC:
 closed = False
 def __init__(self):self.calls = []
 def call(self, method, params, timeout=20):
  self.calls.append(method)
  if method == 'account/rateLimits/read':raise RPCError('rejected')
  return {'data': [{'id': 't', 'status': {'type': 'active'}}]}


class Tests(unittest.TestCase):
 def test_quota_rejection_preserves_connection(self):
  with patch.object(monitor,'state',copy.deepcopy(monitor.state)):
   status=monitor.CodexStatus(threading.Event())
   rpc=FakeRPC();status.use_rpc(rpc);status.refresh(rpc,status.generation)
   self.assertFalse(rpc.closed)
   self.assertIn('thread/list', rpc.calls)
   self.assertIsNone(monitor.state['quota'])
   self.assertEqual(monitor.state['threads'][0]['id'], 't')

 def test_failed_thread_list_keeps_the_last_successful_snapshot(self):
  with patch.object(monitor,'state',copy.deepcopy(monitor.state)):
   status=monitor.CodexStatus(threading.Event())
   rpc=FakeRPC();status.use_rpc(rpc);status.refresh(rpc,status.generation)
   threads=copy.deepcopy(monitor.state['threads'])
   def failed_list(method,params,timeout):
    raise TimeoutError()
   rpc.call=failed_list
   status.refresh(rpc,status.generation)
   self.assertEqual(monitor.state['threads'],threads)
   self.assertEqual(monitor.state['threadStatus'],'unavailable')

 def rpc(self):
  rpc = RPC.__new__(RPC);rpc.approvals = Approvals();rpc.lock = threading.Lock();rpc.pending = {};rpc.closed = False
  return rpc

 def test_resolved_and_disconnect(self):
  rpc = self.rpc();request = {'id': 1, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'echo ok'}}
  rpc.dispatch(request);rpc.dispatch({'method': 'serverRequest/resolved', 'params': {'requestId': 1}})
  approval = rpc.approvals.public()[0]
  self.assertEqual(rpc.approvals.decide(approval['token'], True), 'resolved')
  request = copy.deepcopy(request);request['id'] = 2;rpc.dispatch(request)
  pending = queue.Queue();rpc.pending[9] = pending;rpc.mark_closed()
  self.assertTrue(pending.get_nowait()['disconnected'])
  self.assertEqual(rpc.approvals.public()[1]['decision'], 'disconnected')

 def test_rpc_distinguishes_disconnect_before_write_from_lost_confirmation(self):
  rpc = self.rpc();rpc.seq = 0;rpc.write_lock = threading.Lock()
  stdin = StringIO()
  rpc.p = SimpleNamespace(stdin=stdin)

  def closed_during_preflight():
   rpc.mark_closed()
   return True

  with self.assertRaises(RPCNotSent):
   rpc.call('turn/start', {'_sendGuard': closed_during_preflight})
  self.assertEqual(stdin.getvalue(), '')

  rpc.closed = False
  rpc.p.stdin = SimpleNamespace(write=stdin.write, flush=rpc.mark_closed)
  with self.assertRaises(ConnectionError) as raised:
   rpc.call('turn/start', {})
  self.assertNotIsInstance(raised.exception, RPCNotSent)
  self.assertIn('turn/start', stdin.getvalue())

 def test_notification_restart_dedup(self):
  class Channel:
   calls = 0
   def send(self, *args):self.calls += 1;return {'status': 'unknown_delivery'}
  with tempfile.TemporaryDirectory() as folder:
   path = str(Path(folder) / 'events.db');channel = Channel();notifications = monitor.Notifications(path)
   self.assertEqual(notifications.deliver(channel, 'e', 'title', 'body'), 'unknown_delivery')
   notifications.db.close();notifications = monitor.Notifications(path)
   self.assertEqual(notifications.deliver(channel, 'e', 'title', 'body'), 'duplicate')
   self.assertEqual(channel.calls, 1);notifications.db.close()

 def test_desktop_routes_controls_and_conversations_to_selected_client(self):
  tid = '11111111-2222-4333-8444-555555555555'
  temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
  folder = Path(temporary.name)
  codex = TaskControl(folder / 'codex.db');claude = TaskControl(folder / 'claude.db')
  self.addCleanup(codex.db.close);self.addCleanup(claude.db.close)
  class ClaudeHistory:
   def conversation(self, thread_id):return {'messages': [{'text': 'Claude reply', 'role': 'assistant'}], 'events': []}
  with patch.object(monitor, 'controller', codex), patch.object(monitor, 'claude_controller', claude), \
       patch.object(monitor, 'claude_rpc', ClaudeHistory()), \
       patch.object(monitor, 'read_codex_conversation', return_value={'messages': [{'text': 'Codex reply'}], 'events': []}) as reader:
   operation = monitor.get_controller('claude').submit(tid, 'message', 'queued only', 'client-route')
   self.assertEqual(operation, 'client-route')
   self.assertFalse(codex.snapshot()['operations'])
   self.assertEqual(claude.snapshot()['operations'][0]['threadId'], tid)
   self.assertEqual(monitor.conversation_for(tid, 'claude')['messages'][0]['text'], 'Claude reply')
   reader.assert_not_called()
   self.assertEqual(monitor.conversation_for(tid, 'codex')['messages'][0]['text'], 'Codex reply')
   reader.assert_called_once_with(monitor.ROOT / 'replies.sqlite3', tid, activity=[])
   for function, args in ((monitor.get_controller, ('invalid',)), (monitor.conversation_for, (tid, 'invalid'))):
    with self.assertRaises(ValueError):function(*args)

 def test_fresh_quota_drives_scheduling_and_exposes_persisted_wait(self):
  from tests.test_quota_recovery import Peer, TID, quota
  original=monitor.snapshot();scheduler=Scheduler()
  class QuotaPeer(Peer):
   def __init__(self):
    super().__init__();self.quota=quota();self.order=[]
   def call(self,method,params,timeout=20):
    self.order.append(method)
    if method=='account/rateLimits/read':return copy.deepcopy(self.quota)
    if method=='thread/list':return {'data':[{'id':TID,'status':{'type':self.status}}]}
    return super().call(method,params)
  peer=QuotaPeer()
  task={'threadId':TID,'enabled':True,'exclusiveOwner':True,'prompt':'continue same work'}
  status=monitor.CodexStatus(threading.Event());status.use_rpc(peer)
  def schedule(now):
   status.refresh(peer,status.generation)
   _,quota=status.take_quota(peer)
   with patch('automation.time.time',return_value=now):monitor.run_schedule(peer,[task],scheduler,quota)
  try:
   schedule(100)
   self.assertEqual(peer.order[0],'account/rateLimits/read')
   snap=monitor.snapshot()
   self.assertEqual(snap['quotaWaits'][TID]['resetsAt'],1000)
   self.assertFalse(snap['quota']['ordinaryUsageAllowed'])
   self.assertFalse(peer.sent)
   peer.quota=quota(True,0)
   schedule(1002)
   self.assertEqual(len(peer.sent),1)
   self.assertEqual(peer.sent[0]['threadId'],TID)
   self.assertEqual(monitor.snapshot()['quotaWaits'],{})
   self.assertTrue(monitor.snapshot()['quota']['ordinaryUsageAllowed'])
  finally:monitor.update(**original);scheduler.db.close()

 def test_desktop_worker_lifecycle_never_opens_a_listener(self):
  entered = {client: threading.Event() for client in ('codex', 'claude', 'watch')}
  stopped = {client: threading.Event() for client in entered}
  def worker(client, stop):
   entered[client].set()
   stop.wait(5)
   stopped[client].set()
  stop = threading.Event()
  with patch.dict('os.environ', {'DINGTALK_CLIENT_ID': '', 'DINGTALK_CLIENT_SECRET': ''}), \
       patch.object(monitor, 'poll', side_effect=lambda event: worker('codex', event)), \
       patch.object(monitor, 'poll_claude', side_effect=lambda event: worker('claude', event)), \
       patch.object(monitor, 'watch_codex', side_effect=lambda event: worker('watch', event)), \
       patch('socket.socket') as socket:
   signal, workers = monitor.start_background_service(stop)
   try:
    self.assertIs(signal, stop)
    self.assertTrue(all(event.wait(2) for event in entered.values()))
    self.assertTrue(workers.is_alive())
   finally:
    stop.set();workers.join(timeout=5)
   self.assertFalse(workers.is_alive())
   self.assertTrue(all(event.is_set() for event in stopped.values()))
   socket.assert_not_called()


if __name__ == '__main__':unittest.main()
