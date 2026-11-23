// Package model defines the wire and storage types shared by the
// coordinator server and the syncctl orchestrator.
package model

import "encoding/json"

// Change is one file-level change inside a commit batch.
// Op is one of: "upsert", "delete", "rename".
// For upsert: Path+Hash+Size are set (content lives in the blob store).
// For delete: Path is set.
// For rename: From+Path(=new path)+Hash are set.
// BaseHash is the client-side baseline hash of Path before this change
// (empty if the client did not have the file). It lets the receiving
// client detect concurrent (conflicting) edits.
type Change struct {
	Path     string `json:"path"`
	Op       string `json:"op"`
	Hash     string `json:"hash,omitempty"`
	Size     int64  `json:"size,omitempty"`
	BaseHash string `json:"base_hash,omitempty"`
	From     string `json:"from,omitempty"`
}

// CommitRequest is the body of POST /v1/commit.
type CommitRequest struct {
	Client   string  `json:"client"`
	BatchID  string  `json:"batch_id"`
	Changes  []Change `json:"changes"`
}

// FeedEntry is one flattened change as returned by GET /v1/feed.
type FeedEntry struct {
	ServerSeq int64  `json:"server_seq"`
	CommitSeq int64  `json:"commit_seq"`
	Version   int64  `json:"version"`
	Client    string `json:"client"`
	BatchID   string `json:"batch_id"`
	Change    Change `json:"change"`
}

// CommitRecord is one line of the server commit log (commits.jsonl).
type CommitRecord struct {
	CommitSeq int64          `json:"commit_seq"`
	Version   int64          `json:"version"`
	Time      string         `json:"time"`
	Client    string         `json:"client"`
	BatchID   string         `json:"batch_id"`
	Changes   []Change       `json:"changes"`
	Duplicate bool           `json:"duplicate,omitempty"`
}

// Event is one Codex message event as defined by the task statement.
// All fields except the type-specific payload are required.
type Event struct {
	EventID  string          `json:"event_id"`
	ThreadID string          `json:"thread_id"`
	TurnID   string          `json:"turn_id"`
	Seq      int64           `json:"seq"`
	Type     string          `json:"type"`
	ItemID   string          `json:"item_id"`
	Text     *string         `json:"text,omitempty"`
	CallID   string          `json:"call_id,omitempty"`
	Name     string          `json:"name,omitempty"`
	Args     json.RawMessage `json:"arguments,omitempty"`
	Result   json.RawMessage `json:"result,omitempty"`
}

// EventLogRecord is one line of events.jsonl.
type EventLogRecord struct {
	EventSeq int64  `json:"event_seq"`
	Time     string `json:"time"`
	Client   string `json:"client"`
	BatchID  string `json:"batch_id"`
	Event    Event  `json:"event"`
	Duplicate bool  `json:"duplicate,omitempty"`
}

// FaultSpec configures the single-shot ack-drop fault injector.
type FaultSpec struct {
	Armed  bool   `json:"armed"`
	Mode   string `json:"mode"`   // "commit-ack" | "events-ack"
	Target string `json:"target"` // client id or "any"
	Count  int    `json:"count"`  // remaining drops before auto-disarm
}
