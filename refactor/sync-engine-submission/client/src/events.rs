// Event view building and READ-ONLY replay.
//
//还原规则（按任务书）:
// - 每条事件归入 thread -> turn；轮内逻辑条目按其 item_id 首次出现的 seq 排列。
// - assistant.delta 同一 item_id 按 seq 拼接；每条不同事件各贡献一次。
// - tool.result 按 call_id 关联到 tool.call。
// - 相同 event_id 只保留一份（导入与同步两侧都去重）。
//
// 回放是纯只读的文本重组：不存在任何工具执行路径（本 crate 不产生子进程）。
// 每次回放向 events/replay.log 追加一条记录，tools_dispatched 恒为 0，
// 供评审核查。
use crate::store::{now_rfc3339, StateStore};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

/// One logical item inside a turn.
#[derive(Debug, Clone)]
pub struct TurnItem {
    pub item_id: String,
    pub first_seq: i64,
    pub kind: String, // "user" | "tool_call" | "tool_result" | "assistant"
    pub text: String, // user text or assembled assistant text
    pub call_id: String,
    pub tool_name: String,
    pub arguments: Option<Value>,
    pub result: Option<Value>,
    pub result_call_id: String,
    pub done: bool,
}

impl TurnItem {
    pub fn to_json(&self) -> Value {
        let mut m = Map::new();
        m.insert("item_id".into(), json!(self.item_id));
        m.insert("first_seq".into(), json!(self.first_seq));
        m.insert("kind".into(), json!(self.kind));
        match self.kind.as_str() {
            "tool_call" => {
                m.insert("call_id".into(), json!(self.call_id));
                m.insert("name".into(), json!(self.tool_name));
                m.insert("arguments".into(), self.arguments.clone().unwrap_or(Value::Null));
                if let Some(r) = &self.result {
                    m.insert("result".into(), r.clone());
                }
            }
            "tool_result" => {
                m.insert("call_id".into(), json!(self.result_call_id));
                m.insert("result".into(), self.result.clone().unwrap_or(Value::Null));
            }
            "assistant" => {
                m.insert("text".into(), json!(self.text));
                m.insert("done".into(), json!(self.done));
            }
            _ => {
                m.insert("text".into(), json!(self.text));
            }
        }
        Value::Object(m)
    }
}

#[derive(Debug, Default)]
pub struct Turn {
    pub turn_id: String,
    pub first_seq: i64,
    pub items: Vec<TurnItem>,
    /// item_id -> index into items, for attaching results to calls.
    by_item: BTreeMap<String, usize>,
    /// call_id -> index of the tool_call item.
    by_call: BTreeMap<String, usize>,
}

#[derive(Debug, Default)]
pub struct Thread {
    pub thread_id: String,
    pub turns: Vec<Turn>,
}

/// Build the full view from a set of events (deduped by event_id upstream).
pub fn build_view(events: &[Value]) -> Vec<Thread> {
    let mut threads: BTreeMap<String, Thread> = BTreeMap::new();
    // Stable ordering: sort by (thread, turn, seq, arrival) so duplicated
    // deliveries cannot change the result.
    let mut sorted: Vec<&Value> = events.iter().collect();
    sorted.sort_by_key(|ev| (
        ev.get("thread_id").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        ev.get("turn_id").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        ev.get("seq").and_then(|x| x.as_i64()).unwrap_or(0),
    ));
    for ev in sorted {
        let tid = ev.get("thread_id").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let uid = ev.get("turn_id").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let seq = ev.get("seq").and_then(|x| x.as_i64()).unwrap_or(0);
        let ty = ev.get("type").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let item = ev.get("item_id").and_then(|x| x.as_str()).unwrap_or("").to_string();

        let thread = threads.entry(tid.clone()).or_insert_with(|| Thread {
            thread_id: tid.clone(),
            turns: Vec::new(),
        });
        let turn_pos = thread.turns.iter().position(|t| t.turn_id == uid);
        let ti = match turn_pos {
            Some(i) => i,
            None => {
                thread.turns.push(Turn {
                    turn_id: uid.clone(),
                    first_seq: seq,
                    items: Vec::new(),
                    by_item: BTreeMap::new(),
                    by_call: BTreeMap::new(),
                });
                thread.turns.len() - 1
            }
        };
        let turn = &mut thread.turns[ti];
        match ty.as_str() {
            "user.message" => {
                let text = ev.get("text").and_then(|x| x.as_str()).unwrap_or("").to_string();
                turn.items.push(TurnItem {
                    item_id: item.clone(),
                    first_seq: seq,
                    kind: "user".into(),
                    text,
                    call_id: String::new(),
                    tool_name: String::new(),
                    arguments: None,
                    result: None,
                    result_call_id: String::new(),
                    done: false,
                });
                turn.by_item.insert(item, turn.items.len() - 1);
            }
            "tool.call" => {
                let call_id = ev.get("call_id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let name = ev.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let args = ev.get("arguments").cloned();
                turn.items.push(TurnItem {
                    item_id: item.clone(),
                    first_seq: seq,
                    kind: "tool_call".into(),
                    text: String::new(),
                    call_id,
                    tool_name: name,
                    arguments: args,
                    result: None,
                    result_call_id: String::new(),
                    done: false,
                });
                let idx = turn.items.len() - 1;
                turn.by_item.insert(item, idx);
                turn.by_call.insert(turn.items[idx].call_id.clone(), idx);
            }
            "tool.result" => {
                let call_id = ev.get("call_id").and_then(|x| x.as_str()).unwrap_or("").to_string();
                let result = ev.get("result").cloned();
                // Keep every tool.result as its own logical item (task-book
                // table lists r1 as a separate record) associated to its
                // call through call_id.
                turn.items.push(TurnItem {
                    item_id: item.clone(),
                    first_seq: seq,
                    kind: "tool_result".into(),
                    text: String::new(),
                    call_id: String::new(),
                    tool_name: String::new(),
                    arguments: None,
                    result,
                    result_call_id: call_id,
                    done: false,
                });
                turn.by_item.insert(item, turn.items.len() - 1);
            }
            "assistant.delta" => {
                let text = ev.get("text").and_then(|x| x.as_str()).unwrap_or("").to_string();
                match turn.by_item.get(&item).cloned() {
                    Some(idx) => {
                        // Each distinct delta event contributes its text once.
                        turn.items[idx].text.push_str(&text);
                    }
                    None => {
                        turn.items.push(TurnItem {
                            item_id: item.clone(),
                            first_seq: seq,
                            kind: "assistant".into(),
                            text,
                            call_id: String::new(),
                            tool_name: String::new(),
                            arguments: None,
                            result: None,
                            result_call_id: String::new(),
                            done: false,
                        });
                        turn.by_item.insert(item, turn.items.len() - 1);
                    }
                }
            }
            "assistant.done" => {
                match turn.by_item.get(&item).cloned() {
                    Some(idx) => turn.items[idx].done = true,
                    None => {
                        turn.items.push(TurnItem {
                            item_id: item.clone(),
                            first_seq: seq,
                            kind: "assistant".into(),
                            text: String::new(),
                            call_id: String::new(),
                            tool_name: String::new(),
                            arguments: None,
                            result: None,
                            result_call_id: String::new(),
                            done: true,
                        });
                        turn.by_item.insert(item, turn.items.len() - 1);
                    }
                }
            }
            _ => {} // unknown types are ignored but the event stays in the library
        }
    }
    // Order items within each turn by first appearance; turns by first seq.
    let mut out: Vec<Thread> = threads.into_values().collect();
    for t in &mut out {
        for turn in &mut t.turns {
            turn.items.sort_by_key(|i| i.first_seq);
        }
        t.turns.sort_by_key(|tu| tu.first_seq);
    }
    out
}

/// Render a human-readable dump; `filter` restricts thread/turn.
pub fn dump_text(threads: &[Thread], thread_filter: Option<&str>, turn_filter: Option<&str>) -> String {
    let mut out = String::new();
    for t in threads {
        if let Some(f) = thread_filter {
            if t.thread_id != f {
                continue;
            }
        }
        out.push_str(&format!("== thread {} ==\n", t.thread_id));
        for turn in &t.turns {
            if let Some(f) = turn_filter {
                if turn.turn_id != f {
                    continue;
                }
            }
            out.push_str(&format!("  -- turn {} --\n", turn.turn_id));
            for item in &turn.items {
                match item.kind.as_str() {
                    "user" => out.push_str(&format!("    [{}] USER: {}\n", item.item_id, item.text)),
                    "tool_call" => {
                        out.push_str(&format!(
                            "    [{}] TOOL_CALL {} call_id={} arguments={}\n",
                            item.item_id,
                            item.tool_name,
                            item.call_id,
                            item.arguments.as_ref().map(|a| a.to_string()).unwrap_or_else(|| "{}".into())
                        ));
                        if let Some(r) = &item.result {
                            out.push_str(&format!(
                                "       (result attached by call_id {}): {}\n",
                                item.call_id,
                                r
                            ));
                        }
                    }
                    "tool_result" => out.push_str(&format!(
                        "    [{}] TOOL_RESULT -> call_id {}: {}\n",
                        item.item_id, item.result_call_id, item.result.clone().unwrap_or(Value::Null)
                    )),
                    "assistant" => out.push_str(&format!(
                        "    [{}] ASSISTANT{}: {}\n",
                        item.item_id,
                        if item.done { " (done)" } else { "" },
                        item.text
                    )),
                    _ => {}
                }
            }
        }
    }
    out
}

/// Read-only replay of one turn (or a whole thread). Nothing is executed:
/// tool calls are printed as history and recorded with dispatched=false.
/// A replay record is appended to the replay log for observability.
pub fn replay(
    store: &StateStore,
    client_id: &str,
    events: &[Value],
    thread_filter: Option<&str>,
    turn_filter: Option<&str>,
) -> Result<Value, String> {
    let view = build_view(events);
    let mut printed = String::new();
    let mut tool_calls = 0usize;
    let mut items = 0usize;
    for t in &view {
        if let Some(f) = thread_filter {
            if t.thread_id != f {
                continue;
            }
        }
        for turn in &t.turns {
            if let Some(f) = turn_filter {
                if turn.turn_id != f {
                    continue;
                }
            }
            printed.push_str(&format!(
                "REPLAY thread={} turn={}  (read-only: no tools are dispatched)\n",
                t.thread_id, turn.turn_id
            ));
            for item in &turn.items {
                items += 1;
                match item.kind.as_str() {
                    "user" => printed.push_str(&format!("  USER  > {}\n", item.text)),
                    "tool_call" => {
                        tool_calls += 1;
                        printed.push_str(&format!(
                            "  TOOL  > {} call_id={} args={} [historical record, NOT dispatched]\n",
                            item.tool_name,
                            item.call_id,
                            item.arguments.as_ref().map(|a| a.to_string()).unwrap_or_else(|| "{}".into())
                        ));
                        if let Some(r) = &item.result {
                            printed.push_str(&format!("  RESULT> (call_id {}) {}\n", item.call_id, r));
                        }
                    }
                    "tool_result" => printed.push_str(&format!(
                        "  RESULT> (call_id {}) {}\n",
                        item.result_call_id,
                        item.result.clone().unwrap_or(Value::Null)
                    )),
                    "assistant" => printed.push_str(&format!("  BOT   > {}\n", item.text)),
                    _ => {}
                }
            }
        }
    }
    let record = json!({
        "time": now_rfc3339(),
        "client": client_id,
        "thread": thread_filter.unwrap_or(""),
        "turn": turn_filter.unwrap_or(""),
        "items": items,
        "tool_calls_seen": tool_calls,
        "tools_dispatched": 0,
        "mode": "read-only",
    });
    store
        .append_replay_log(&record)
        .map_err(|e| format!("write replay log: {}", e))?;
    Ok(json!({
        "mode": "read-only",
        "tools_dispatched": 0,
        "items": items,
        "tool_calls_seen": tool_calls,
        "transcript": printed,
        "replay_log": "state/events/replay.log",
    }))
}
