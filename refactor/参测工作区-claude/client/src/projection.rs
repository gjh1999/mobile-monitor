use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

/// Read-only projection of raw events into thread/turn/item history.
/// This function never dispatches tools; it only reads JSON values.
pub fn project(events: &[Value], thread_id: &str, turn_id: Option<&str>) -> Value {
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut filtered: Vec<&Value> = Vec::new();
    for e in events {
        if e.get("thread_id").and_then(Value::as_str) != Some(thread_id) {
            continue;
        }
        let eid = e
            .get("event_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !seen.insert(eid) {
            continue;
        }
        filtered.push(e);
    }
    filtered.sort_by(|a, b| {
        let sa = a.get("seq").and_then(Value::as_u64).unwrap_or(u64::MAX);
        let sb = b.get("seq").and_then(Value::as_u64).unwrap_or(u64::MAX);
        sa.cmp(&sb).then_with(|| {
            a.get("event_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(b.get("event_id").and_then(Value::as_str).unwrap_or(""))
        })
    });

    let mut order: Vec<String> = Vec::new();
    let mut groups: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for e in filtered {
        let t = e
            .get("turn_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !groups.contains_key(&t) {
            order.push(t.clone());
        }
        groups.entry(t).or_default().push(e);
    }

    match turn_id {
        Some(t) => {
            let items = groups
                .get(t)
                .map(|evs| project_turn(evs))
                .unwrap_or_default();
            json!({
                "thread_id": thread_id,
                "turn_id": t,
                "items": items,
            })
        }
        None => {
            let turns: Vec<Value> = order
                .iter()
                .map(|t| {
                    json!({
                        "turn_id": t,
                        "items": groups.get(t).map(|evs| project_turn(evs)).unwrap_or_default(),
                    })
                })
                .collect();
            json!({
                "thread_id": thread_id,
                "turns": turns,
            })
        }
    }
}

fn project_turn(events: &[&Value]) -> Vec<Value> {
    let mut items: Vec<Value> = Vec::new();
    let mut index: BTreeMap<String, usize> = BTreeMap::new();
    for e in events {
        let item_id = e
            .get("item_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let ty = e.get("type").and_then(Value::as_str).unwrap_or("");
        match ty {
            "user.message" => {
                if !index.contains_key(&item_id) {
                    items.push(json!({
                        "item_id": item_id,
                        "type": "user.message",
                        "text": e.get("text").cloned().unwrap_or(Value::String(String::new())),
                    }));
                    index.insert(item_id, items.len() - 1);
                }
            }
            "tool.call" => {
                if !index.contains_key(&item_id) {
                    items.push(json!({
                        "item_id": item_id,
                        "type": "tool.call",
                        "call_id": e.get("call_id").cloned().unwrap_or(Value::Null),
                        "name": e.get("name").cloned().unwrap_or(Value::Null),
                        "arguments": e.get("arguments").cloned().unwrap_or(Value::Null),
                    }));
                    index.insert(item_id, items.len() - 1);
                }
            }
            "tool.result" => {
                if !index.contains_key(&item_id) {
                    items.push(json!({
                        "item_id": item_id,
                        "type": "tool.result",
                        "call_id": e.get("call_id").cloned().unwrap_or(Value::Null),
                        "result": e.get("result").cloned().unwrap_or(Value::Null),
                    }));
                    index.insert(item_id, items.len() - 1);
                }
            }
            "assistant.delta" => {
                let text = e.get("text").and_then(Value::as_str).unwrap_or("");
                if let Some(&i) = index.get(&item_id) {
                    if items[i].get("type").and_then(Value::as_str) == Some("assistant") {
                        let cur = items[i]
                            .get("text")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string();
                        items[i]["text"] = Value::String(format!("{}{}", cur, text));
                    }
                } else {
                    items.push(json!({
                        "item_id": item_id,
                        "type": "assistant",
                        "text": text,
                        "status": "streaming",
                    }));
                    index.insert(item_id, items.len() - 1);
                }
            }
            "assistant.done" => {
                if let Some(&i) = index.get(&item_id) {
                    items[i]["status"] = Value::String("completed".into());
                } else {
                    items.push(json!({
                        "item_id": item_id,
                        "type": "assistant",
                        "text": "",
                        "status": "completed",
                    }));
                    index.insert(item_id, items.len() - 1);
                }
            }
            _ => {
                if !index.contains_key(&item_id) {
                    let mut obj = (*e).clone();
                    if let Some(m) = obj.as_object_mut() {
                        m.insert("type".into(), Value::String(ty.to_string()));
                    }
                    items.push(obj);
                    index.insert(item_id, items.len() - 1);
                }
            }
        }
    }
    items
}

pub fn count_tool_calls(history: &Value) -> usize {
    fn count_items(items: &[Value]) -> usize {
        items
            .iter()
            .filter(|i| i.get("type").and_then(Value::as_str) == Some("tool.call"))
            .count()
    }
    if let Some(items) = history.get("items").and_then(Value::as_array) {
        return count_items(items);
    }
    if let Some(turns) = history.get("turns").and_then(Value::as_array) {
        return turns
            .iter()
            .map(|t| {
                t.get("items")
                    .and_then(Value::as_array)
                    .map(|items| count_items(items))
                    .unwrap_or(0)
            })
            .sum();
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_lines(s: &str) -> Vec<Value> {
        s.lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    const PUBLIC: &str = r#"{"event_id":"demo-1","thread_id":"thread-weather","turn_id":"turn-01","seq":1,"type":"user.message","item_id":"u1","text":"计算读数的平均值。"}
{"event_id":"demo-2","thread_id":"thread-weather","turn_id":"turn-01","seq":2,"type":"tool.call","item_id":"c1","call_id":"call-1","name":"sample.mean","arguments":{"values":[18,22]}}
{"event_id":"demo-3","thread_id":"thread-weather","turn_id":"turn-01","seq":3,"type":"tool.result","item_id":"r1","call_id":"call-1","result":{"mean":20}}
{"event_id":"demo-4","thread_id":"thread-weather","turn_id":"turn-01","seq":4,"type":"assistant.delta","item_id":"a1","text":"平均值是 "}
{"event_id":"demo-5","thread_id":"thread-weather","turn_id":"turn-01","seq":5,"type":"assistant.delta","item_id":"a1","text":"20。"}
{"event_id":"demo-6","thread_id":"thread-weather","turn_id":"turn-01","seq":6,"type":"assistant.done","item_id":"a1"}"#;

    #[test]
    fn public_turn_has_four_logical_items() {
        let h = project(
            &parse_lines(PUBLIC),
            "thread-weather",
            Some("turn-01"),
        );
        let items = h["items"].as_array().unwrap();
        assert_eq!(items.len(), 4, "{}", h);
        assert_eq!(items[0]["item_id"], "u1");
        assert_eq!(items[0]["text"], "计算读数的平均值。");
        assert_eq!(items[1]["type"], "tool.call");
        assert_eq!(items[1]["call_id"], "call-1");
        assert_eq!(items[1]["name"], "sample.mean");
        assert_eq!(items[1]["arguments"]["values"][0], 18);
        assert_eq!(items[2]["type"], "tool.result");
        assert_eq!(items[2]["call_id"], "call-1");
        assert_eq!(items[2]["result"]["mean"], 20);
        assert_eq!(items[3]["type"], "assistant");
        assert_eq!(items[3]["text"], "平均值是 20。");
        assert_eq!(items[3]["status"], "completed");
    }

    #[test]
    fn replay_dedupes_event_id_but_keeps_equal_text_events() {
        let e1 = r#"{"event_id":"l1","thread_id":"t","turn_id":"u","seq":1,"type":"user.message","item_id":"u1","text":"hi"}"#;
        let e2 = r#"{"event_id":"l2","thread_id":"t","turn_id":"u","seq":2,"type":"tool.call","item_id":"c1","call_id":"mark","name":"fixture.append_marker","arguments":{}}"#;
        let e3 = r#"{"event_id":"l3","thread_id":"t","turn_id":"u","seq":3,"type":"tool.result","item_id":"r1","call_id":"mark","result":{"accepted":true}}"#;
        let e4 = r#"{"event_id":"l4","thread_id":"t","turn_id":"u","seq":4,"type":"assistant.delta","item_id":"a1","text":"哈"}"#;
        let e5 = r#"{"event_id":"l5","thread_id":"t","turn_id":"u","seq":5,"type":"assistant.delta","item_id":"a1","text":"哈"}"#;
        let e6 = r#"{"event_id":"l6","thread_id":"t","turn_id":"u","seq":6,"type":"assistant.delta","item_id":"a1","text":"，好"}"#;
        let e7 = r#"{"event_id":"l7","thread_id":"t","turn_id":"u","seq":7,"type":"assistant.done","item_id":"a1"}"#;
        // part1 = 1..4, part2 = 3..7, then full replay = 1..7
        let mut all = Vec::new();
        for s in [e1, e2, e3, e4, e3, e4, e5, e6, e7, e1, e2, e3, e4, e5, e6, e7] {
            all.push(serde_json::from_str::<Value>(s).unwrap());
        }
        let h = project(&all, "t", Some("u"));
        let items = h["items"].as_array().unwrap();
        assert_eq!(items.len(), 4, "{}", h);
        assert_eq!(items[3]["text"], "哈哈，好");
        assert_eq!(items[3]["status"], "completed");
        assert_eq!(items[2]["call_id"], "mark");
        assert_eq!(count_tool_calls(&h), 1);
    }

    #[test]
    fn incomplete_reply_stays_streaming() {
        let events = parse_lines(
            r#"{"event_id":"a","thread_id":"t","turn_id":"u","seq":1,"type":"assistant.delta","item_id":"x","text":"哈"}"#,
        );
        let h = project(&events, "t", Some("u"));
        assert_eq!(h["items"][0]["status"], "streaming");
        assert_eq!(h["items"][0]["text"], "哈");
    }
}
