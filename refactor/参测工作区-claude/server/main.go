package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Entry is the coordinator's persisted view of one project path.
type Entry struct {
	Version   uint64 `json:"version"`
	Deleted   bool   `json:"deleted"`
	Hash      string `json:"hash,omitempty"`
	PriorHash string `json:"prior_hash,omitempty"`
	OpID      string `json:"op_id,omitempty"`
	DeviceID  string `json:"device_id,omitempty"`
	UpdatedAt string `json:"updated_at,omitempty"`
}

type AppliedOp struct {
	Version     uint64 `json:"version"`
	Result      string `json:"result"`
	Path        string `json:"path"`
	DeviceID    string `json:"device_id"`
	BaseVersion uint64 `json:"base_version"`
	At          string `json:"at"`
}

type OpLogEntry struct {
	OpID        string `json:"op_id"`
	DeviceID    string `json:"device_id"`
	Path        string `json:"path"`
	BaseVersion uint64 `json:"base_version"`
	Version     uint64 `json:"version"`
	Result      string `json:"result"`
	At          string `json:"at"`
	Note        string `json:"note,omitempty"`
}

type ConflictLog struct {
	Path          string `json:"path"`
	DeviceID      string `json:"device_id"`
	OpID          string `json:"op_id"`
	BaseVersion   uint64 `json:"base_version"`
	ServerVersion uint64 `json:"server_version"`
	ServerDeleted bool   `json:"server_deleted"`
	ServerHash    string `json:"server_hash,omitempty"`
	At            string `json:"at"`
}

type FaultLog struct {
	Action string `json:"action"`
	At     string `json:"at"`
	OpID   string `json:"op_id,omitempty"`
	Path   string `json:"path,omitempty"`
	Note   string `json:"note,omitempty"`
}

type DeviceInfo struct {
	LastPullSince uint64 `json:"last_pull_since"`
	LastSeenAt    string `json:"last_seen_at,omitempty"`
	LastEventAt   string `json:"last_event_at,omitempty"`
	EventBatches  int    `json:"event_batches"`
}

// Persistent is the complete durable state of the coordinator.
type Persistent struct {
	NextVersion            uint64                     `json:"next_version"`
	Files                  map[string]Entry           `json:"files"`
	AppliedOps             map[string]AppliedOp       `json:"applied_ops"`
	OpLog                  []OpLogEntry               `json:"op_log"`
	Conflicts              []ConflictLog              `json:"conflicts"`
	Events                 map[string]json.RawMessage `json:"events"`
	Devices                map[string]DeviceInfo      `json:"devices"`
	FaultDropNextCommitAck bool                       `json:"fault_drop_next_commit_ack"`
	FaultLog               []FaultLog                 `json:"fault_log"`
	CreatedAt              string                     `json:"created_at"`
}

type Server struct {
	mu       sync.Mutex
	stateDir string
	blobDir  string
	state    Persistent
}

func now() string { return time.Now().UTC().Format(time.RFC3339Nano) }

func newServer(stateDir string) (*Server, error) {
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return nil, err
	}
	blobDir := filepath.Join(stateDir, "blobs")
	if err := os.MkdirAll(blobDir, 0o755); err != nil {
		return nil, err
	}
	s := &Server{stateDir: stateDir, blobDir: blobDir}
	statePath := filepath.Join(stateDir, "state.json")
	data, err := os.ReadFile(statePath)
	if err == nil {
		if err := json.Unmarshal(data, &s.state); err != nil {
			return nil, fmt.Errorf("decode state: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if s.state.NextVersion == 0 {
		s.state.NextVersion = 1
	}
	if s.state.Files == nil {
		s.state.Files = map[string]Entry{}
	}
	if s.state.AppliedOps == nil {
		s.state.AppliedOps = map[string]AppliedOp{}
	}
	if s.state.Events == nil {
		s.state.Events = map[string]json.RawMessage{}
	}
	if s.state.Devices == nil {
		s.state.Devices = map[string]DeviceInfo{}
	}
	if s.state.CreatedAt == "" {
		s.state.CreatedAt = now()
	}
	if err := s.saveLocked(); err != nil {
		return nil, err
	}
	return s, nil
}

// saveLocked atomically persists state.json. Caller holds s.mu.
func (s *Server) saveLocked() error {
	data, err := json.MarshalIndent(&s.state, "", "  ")
	if err != nil {
		return err
	}
	statePath := filepath.Join(s.stateDir, "state.json")
	tmp, err := os.CreateTemp(s.stateDir, "state-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, statePath)
}

func (s *Server) save() error { return s.saveLocked() }

func hashBytes(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func (s *Server) writeBlob(hash string, content []byte) error {
	if hash == "" || hash != hashBytes(content) {
		return fmt.Errorf("content hash mismatch")
	}
	target := filepath.Join(s.blobDir, hash)
	if _, err := os.Stat(target); err == nil {
		return nil
	}
	tmp, err := os.CreateTemp(s.blobDir, "blob-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(content); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, target)
}

func (s *Server) readBlob(hash string) ([]byte, error) {
	return os.ReadFile(filepath.Join(s.blobDir, hash))
}

func cleanRelPath(p string) (string, error) {
	p = strings.ReplaceAll(p, "\\", "/")
	if p == "" || strings.HasPrefix(p, "/") {
		return "", fmt.Errorf("invalid path %q", p)
	}
	if strings.Contains(p, ":") {
		return "", fmt.Errorf("invalid path %q", p)
	}
	c := path.Clean(p)
	if c == "." || c == ".." || strings.HasPrefix(c, "../") {
		return "", fmt.Errorf("invalid path %q", p)
	}
	return c, nil
}

func entryFromServer(e Entry) map[string]any {
	m := map[string]any{
		"version": e.Version,
		"deleted": e.Deleted,
	}
	if e.Hash != "" {
		m["hash"] = e.Hash
	}
	if e.PriorHash != "" {
		m["prior_hash"] = e.PriorHash
	}
	if e.OpID != "" {
		m["op_id"] = e.OpID
	}
	if e.DeviceID != "" {
		m["device_id"] = e.DeviceID
	}
	if e.UpdatedAt != "" {
		m["updated_at"] = e.UpdatedAt
	}
	return m
}

func (s *Server) commitCountLocked() int { return len(s.state.AppliedOps) }

func (s *Server) serverSeqLocked() uint64 {
	if s.state.NextVersion == 0 {
		return 0
	}
	return s.state.NextVersion - 1
}

// applyPushLocked applies one operation or reports a conflict. Caller holds mu.
func (s *Server) applyPushLocked(deviceID string, op PushOp) (map[string]any, bool) {
	// Idempotency: an already-applied op returns the original acknowledgement.
	if prev, ok := s.state.AppliedOps[op.OpID]; ok {
		return map[string]any{
			"result":       "already_applied",
			"version":      prev.Version,
			"op_id":        op.OpID,
			"path":         prev.Path,
			"commit_count": s.commitCountLocked(),
			"server_seq":   s.serverSeqLocked(),
			"original_ack": prev,
		}, false
	}

	cur, exists := s.state.Files[op.Path]
	curVersion := uint64(0)
	if exists {
		curVersion = cur.Version
	}
	if op.BaseVersion != curVersion {
		serverView := map[string]any{"version": uint64(0), "deleted": true}
		if exists {
			serverView = entryFromServer(cur)
			if !cur.Deleted {
				if content, err := s.readBlob(cur.Hash); err == nil {
					serverView["content_b64"] = base64.StdEncoding.EncodeToString(content)
				}
			}
		}
		cl := ConflictLog{
			Path: op.Path, DeviceID: deviceID, OpID: op.OpID,
			BaseVersion: op.BaseVersion, ServerVersion: curVersion,
			ServerDeleted: !exists || cur.Deleted, ServerHash: cur.Hash, At: now(),
		}
		if exists && cur.Deleted {
			cl.ServerHash = cur.PriorHash
		}
		s.state.Conflicts = append(s.state.Conflicts, cl)
		s.state.OpLog = append(s.state.OpLog, OpLogEntry{
			OpID: op.OpID, DeviceID: deviceID, Path: op.Path,
			BaseVersion: op.BaseVersion, Version: curVersion, Result: "conflict", At: now(),
			Note: "base_version does not match current server version",
		})
		if err := s.saveLocked(); err != nil {
			log.Printf("ERROR persist conflict: %v", err)
		}
		log.Printf("CONFLICT device=%s path=%s op=%s base=%d server=%d deleted=%v",
			deviceID, op.Path, op.OpID, op.BaseVersion, curVersion, cl.ServerDeleted)
		return map[string]any{
			"result":       "conflict",
			"op_id":        op.OpID,
			"path":         op.Path,
			"server":       serverView,
			"commit_count": s.commitCountLocked(),
			"server_seq":   s.serverSeqLocked(),
		}, false
	}

	// Apply the operation.
	var content []byte
	if !op.Deleted {
		if op.Hash == "" || op.ContentB64 == "" {
			return map[string]any{"result": "error", "error": "put operation requires hash and content_b64"}, false
		}
		decoded, err := base64.StdEncoding.DecodeString(op.ContentB64)
		if err != nil {
			return map[string]any{"result": "error", "error": "bad base64 content"}, false
		}
		content = decoded
		if hashBytes(content) != op.Hash {
			return map[string]any{"result": "error", "error": "content hash does not match declared hash"}, false
		}
		if err := s.writeBlob(op.Hash, content); err != nil {
			return map[string]any{"result": "error", "error": err.Error()}, false
		}
	}

	version := s.state.NextVersion
	s.state.NextVersion++
	entry := Entry{
		Version: version, Deleted: op.Deleted, OpID: op.OpID,
		DeviceID: deviceID, UpdatedAt: now(),
	}
	if op.Deleted {
		if exists {
			entry.PriorHash = cur.Hash
			if entry.PriorHash == "" {
				entry.PriorHash = cur.PriorHash
			}
		}
	} else {
		entry.Hash = op.Hash
	}
	s.state.Files[op.Path] = entry
	s.state.AppliedOps[op.OpID] = AppliedOp{
		Version: version, Result: "applied", Path: op.Path, DeviceID: deviceID,
		BaseVersion: op.BaseVersion, At: now(),
	}
	s.state.OpLog = append(s.state.OpLog, OpLogEntry{
		OpID: op.OpID, DeviceID: deviceID, Path: op.Path,
		BaseVersion: op.BaseVersion, Version: version, Result: "applied", At: now(),
	})
	if err := s.saveLocked(); err != nil {
		log.Printf("ERROR persist applied op: %v", err)
	}
	log.Printf("COMMIT device=%s path=%s op=%s base=%d version=%d deleted=%v hash=%s",
		deviceID, op.Path, op.OpID, op.BaseVersion, version, op.Deleted, entry.Hash)

	drop := s.state.FaultDropNextCommitAck
	if drop {
		s.state.FaultDropNextCommitAck = false
		s.state.FaultLog = append(s.state.FaultLog, FaultLog{
			Action: "drop_next_commit_ack", At: now(), OpID: op.OpID, Path: op.Path,
			Note: "commit persisted; success response dropped before client read it",
		})
		if err := s.saveLocked(); err != nil {
			log.Printf("ERROR persist fault log: %v", err)
		}
		log.Printf("FAULT drop-ack fired op=%s path=%s version=%d", op.OpID, op.Path, version)
	}

	resp := map[string]any{
		"result":       "applied",
		"version":      version,
		"op_id":        op.OpID,
		"path":         op.Path,
		"commit_count": s.commitCountLocked(),
		"server_seq":   s.serverSeqLocked(),
	}
	return resp, drop
}

// ---- HTTP request/response shapes ----

type PushOp struct {
	OpID        string `json:"op_id"`
	Path        string `json:"path"`
	BaseVersion uint64 `json:"base_version"`
	Deleted     bool   `json:"deleted"`
	Hash        string `json:"hash"`
	ContentB64  string `json:"content_b64"`
}

type PushReq struct {
	DeviceID string `json:"device_id"`
	Op       PushOp `json:"op"`
}

type EventIn struct {
	EventID  string          `json:"event_id"`
	ThreadID string          `json:"thread_id"`
	TurnID   string          `json:"turn_id"`
	Seq      uint64          `json:"seq"`
	Type     string          `json:"type"`
	ItemID   string          `json:"item_id"`
	Raw      json.RawMessage `json:"-"`
}

func decodeEvent(raw json.RawMessage) (EventIn, error) {
	var e EventIn
	if err := json.Unmarshal(raw, &e); err != nil {
		return e, err
	}
	e.Raw = raw
	if e.EventID == "" || e.ThreadID == "" || e.TurnID == "" || e.Type == "" || e.ItemID == "" {
		return e, fmt.Errorf("event missing required field")
	}
	return e, nil
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"ok": true, "service": "coordinator", "time": now()})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	files := map[string]any{}
	live := 0
	for p, e := range s.state.Files {
		files[p] = entryFromServer(e)
		if !e.Deleted {
			live++
		}
	}
	writeJSON(w, 200, map[string]any{
		"ok":           true,
		"service":      "coordinator",
		"server_seq":   s.serverSeqLocked(),
		"next_version": s.state.NextVersion,
		"live_files":   live,
		"files":        files,
		"applied_ops":  len(s.state.AppliedOps),
		"op_log_len":   len(s.state.OpLog),
		"conflicts":    s.state.Conflicts,
		"events_total": len(s.state.Events),
		"devices":      s.state.Devices,
		"fault":        map[string]any{"drop_next_commit_ack": s.state.FaultDropNextCommitAck},
		"fault_log":    s.state.FaultLog,
		"state_dir":    s.stateDir,
		"created_at":   s.state.CreatedAt,
	})
}

func (s *Server) handlePush(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST required"})
		return
	}
	var req PushReq
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, 400, map[string]any{"error": err.Error()})
		return
	}
	if req.DeviceID == "" || req.Op.OpID == "" {
		writeJSON(w, 400, map[string]any{"error": "device_id and op.op_id required"})
		return
	}
	p, err := cleanRelPath(req.Op.Path)
	if err != nil {
		writeJSON(w, 400, map[string]any{"error": err.Error()})
		return
	}
	req.Op.Path = p

	s.mu.Lock()
	resp, drop := s.applyPushLocked(req.DeviceID, req.Op)
	s.mu.Unlock()

	if drop {
		// Persisted commit, but the client must not observe a success receipt.
		hj, ok := w.(http.Hijacker)
		if !ok {
			writeJSON(w, 500, map[string]any{"error": "hijack unavailable"})
			return
		}
		conn, _, err := hj.Hijack()
		if err != nil {
			writeJSON(w, 500, map[string]any{"error": err.Error()})
			return
		}
		_ = conn.Close()
		return
	}
	status := 200
	if resp["result"] == "error" {
		status = 400
	}
	writeJSON(w, status, resp)
}

func (s *Server) handlePull(w http.ResponseWriter, r *http.Request) {
	deviceID := r.URL.Query().Get("device_id")
	since, err := parseUint(r.URL.Query().Get("since"))
	if err != nil {
		writeJSON(w, 400, map[string]any{"error": "bad since"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	type pullEntry struct {
		Path       string `json:"path"`
		Version    uint64 `json:"version"`
		Deleted    bool   `json:"deleted"`
		Hash       string `json:"hash,omitempty"`
		PriorHash  string `json:"prior_hash,omitempty"`
		ContentB64 string `json:"content_b64,omitempty"`
		OpID       string `json:"op_id,omitempty"`
		DeviceID   string `json:"device_id,omitempty"`
	}
	entries := make([]pullEntry, 0)
	for p, e := range s.state.Files {
		if e.Version <= since {
			continue
		}
		pe := pullEntry{Path: p, Version: e.Version, Deleted: e.Deleted, Hash: e.Hash,
			PriorHash: e.PriorHash, OpID: e.OpID, DeviceID: e.DeviceID}
		if !e.Deleted && e.Hash != "" {
			if content, err := s.readBlob(e.Hash); err == nil {
				pe.ContentB64 = base64.StdEncoding.EncodeToString(content)
			}
		}
		entries = append(entries, pe)
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })
	if deviceID != "" {
		d := s.state.Devices[deviceID]
		if since > d.LastPullSince {
			d.LastPullSince = since
		}
		d.LastSeenAt = now()
		s.state.Devices[deviceID] = d
		if err := s.saveLocked(); err != nil {
			log.Printf("ERROR persist device: %v", err)
		}
	}
	writeJSON(w, 200, map[string]any{
		"ok":         true,
		"server_seq": s.serverSeqLocked(),
		"since":      since,
		"entries":    entries,
	})
}

func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		threadID := r.URL.Query().Get("thread_id")
		s.mu.Lock()
		type ev struct {
			EventID  string          `json:"event_id"`
			ThreadID string          `json:"thread_id"`
			TurnID   string          `json:"turn_id"`
			Seq      uint64          `json:"seq"`
			Type     string          `json:"type"`
			ItemID   string          `json:"item_id"`
			Raw      json.RawMessage `json:"raw"`
		}
		list := make([]ev, 0)
		for id, raw := range s.state.Events {
			e, err := decodeEvent(raw)
			if err != nil {
				continue
			}
			if threadID != "" && e.ThreadID != threadID {
				continue
			}
			list = append(list, ev{EventID: id, ThreadID: e.ThreadID, TurnID: e.TurnID,
				Seq: e.Seq, Type: e.Type, ItemID: e.ItemID, Raw: raw})
		}
		s.mu.Unlock()
		sort.Slice(list, func(i, j int) bool {
			if list[i].Seq != list[j].Seq {
				return list[i].Seq < list[j].Seq
			}
			return list[i].EventID < list[j].EventID
		})
		out := make([]json.RawMessage, 0, len(list))
		for _, e := range list {
			out = append(out, e.Raw)
		}
		writeJSON(w, 200, map[string]any{
			"ok": true, "thread_id": threadID, "count": len(out), "events": out,
		})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "GET or POST required"})
		return
	}
	var req struct {
		DeviceID string            `json:"device_id"`
		Events   []json.RawMessage `json:"events"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, 400, map[string]any{"error": err.Error()})
		return
	}
	if len(req.Events) > 100000 {
		writeJSON(w, 400, map[string]any{"error": "batch too large"})
		return
	}
	accepted, duplicates := 0, 0
	s.mu.Lock()
	for _, raw := range req.Events {
		e, err := decodeEvent(raw)
		if err != nil {
			s.mu.Unlock()
			writeJSON(w, 400, map[string]any{"error": "invalid event: " + err.Error()})
			return
		}
		if _, ok := s.state.Events[e.EventID]; ok {
			duplicates++
			continue
		}
		cp := make([]byte, len(raw))
		copy(cp, raw)
		s.state.Events[e.EventID] = json.RawMessage(cp)
		accepted++
	}
	if req.DeviceID != "" {
		d := s.state.Devices[req.DeviceID]
		d.LastEventAt = now()
		d.EventBatches++
		s.state.Devices[req.DeviceID] = d
	}
	if err := s.saveLocked(); err != nil {
		log.Printf("ERROR persist events: %v", err)
	}
	total := len(s.state.Events)
	s.mu.Unlock()
	log.Printf("EVENTS device=%s accepted=%d duplicates=%d total=%d", req.DeviceID, accepted, duplicates, total)
	writeJSON(w, 200, map[string]any{
		"ok": true, "accepted": accepted, "duplicates": duplicates, "total": total,
	})
}

func (s *Server) handleThreads(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	type turnInfo struct {
		TurnID     string `json:"turn_id"`
		EventCount int    `json:"event_count"`
		ItemCount  int    `json:"item_count"`
	}
	threads := map[string]map[string]map[string]struct{}{}
	for _, raw := range s.state.Events {
		e, err := decodeEvent(raw)
		if err != nil {
			continue
		}
		if threads[e.ThreadID] == nil {
			threads[e.ThreadID] = map[string]map[string]struct{}{}
		}
		if threads[e.ThreadID][e.TurnID] == nil {
			threads[e.ThreadID][e.TurnID] = map[string]struct{}{}
		}
		threads[e.ThreadID][e.TurnID][e.ItemID] = struct{}{}
	}
	s.mu.Unlock()
	type threadOut struct {
		ThreadID string     `json:"thread_id"`
		Turns    []turnInfo `json:"turns"`
	}
	out := make([]threadOut, 0, len(threads))
	for tid, turns := range threads {
		to := threadOut{ThreadID: tid}
		for turnID, items := range turns {
			to.Turns = append(to.Turns, turnInfo{TurnID: turnID, ItemCount: len(items)})
		}
		sort.Slice(to.Turns, func(i, j int) bool { return to.Turns[i].TurnID < to.Turns[j].TurnID })
		out = append(out, to)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ThreadID < out[j].ThreadID })
	writeJSON(w, 200, map[string]any{"ok": true, "threads": out})
}

func (s *Server) handleFault(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST required"})
		return
	}
	var req struct {
		Enabled bool `json:"enabled"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, 400, map[string]any{"error": err.Error()})
		return
	}
	s.mu.Lock()
	s.state.FaultDropNextCommitAck = req.Enabled
	s.state.FaultLog = append(s.state.FaultLog, FaultLog{
		Action: "arm_drop_next_commit_ack", At: now(),
		Note: fmt.Sprintf("enabled=%v", req.Enabled),
	})
	if err := s.saveLocked(); err != nil {
		log.Printf("ERROR persist fault arm: %v", err)
	}
	s.mu.Unlock()
	log.Printf("FAULT arm drop_next_commit_ack=%v", req.Enabled)
	writeJSON(w, 200, map[string]any{"ok": true, "drop_next_commit_ack": req.Enabled})
}

func (s *Server) handleOpLog(w http.ResponseWriter, r *http.Request) {
	pathFilter := r.URL.Query().Get("path")
	limit, _ := parseUint(r.URL.Query().Get("limit"))
	s.mu.Lock()
	entries := make([]OpLogEntry, 0, len(s.state.OpLog))
	for _, e := range s.state.OpLog {
		if pathFilter != "" && e.Path != pathFilter {
			continue
		}
		entries = append(entries, e)
	}
	s.mu.Unlock()
	if limit > 0 && uint64(len(entries)) > limit {
		entries = entries[uint64(len(entries))-limit:]
	}
	writeJSON(w, 200, map[string]any{"ok": true, "entries": entries, "count": len(entries)})
}

func (s *Server) handleConflicts(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	conflicts := append([]ConflictLog(nil), s.state.Conflicts...)
	s.mu.Unlock()
	writeJSON(w, 200, map[string]any{"ok": true, "conflicts": conflicts})
}

func (s *Server) handleShutdown(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST required"})
		return
	}
	s.mu.Lock()
	err := s.saveLocked()
	s.mu.Unlock()
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "shutting_down": true})
	log.Printf("SHUTDOWN requested via admin API")
	go func() {
		time.Sleep(150 * time.Millisecond)
		os.Exit(0)
	}()
}

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.handleHealth)
	mux.HandleFunc("/api/status", s.handleStatus)
	mux.HandleFunc("/api/push", s.handlePush)
	mux.HandleFunc("/api/pull", s.handlePull)
	mux.HandleFunc("/api/events", s.handleEvents)
	mux.HandleFunc("/api/threads", s.handleThreads)
	mux.HandleFunc("/api/admin/fault/drop-next-commit-ack", s.handleFault)
	mux.HandleFunc("/api/admin/oplog", s.handleOpLog)
	mux.HandleFunc("/api/admin/conflicts", s.handleConflicts)
	mux.HandleFunc("/api/admin/shutdown", s.handleShutdown)
	return mux
}

func main() {
	addr := flag.String("addr", "127.0.0.1:7801", "listen address")
	stateDir := flag.String("state", "run/server", "durable state directory")
	flag.Parse()

	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	s, err := newServer(*stateDir)
	if err != nil {
		log.Fatalf("start: %v", err)
	}
	absState, _ := filepath.Abs(*stateDir)
	log.Printf("COORDINATOR listening=%s state=%s", *addr, absState)
	srv := &http.Server{Addr: *addr, Handler: s.routes()}
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	log.Printf("COORDINATOR ready")
	if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("serve: %v", err)
	}
}
