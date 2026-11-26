package main

import (
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"hash/fnv"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// ---------------------------------------------------------------------------
// 数据结构

type FileOp struct {
	OpID    string `json:"op_id"`
	Path    string `json:"path"`
	Kind    string `json:"kind"` // put | delete
	Blob    string `json:"blob,omitempty"`
	Base    string `json:"base,omitempty"`
	Content string `json:"content,omitempty"` // base64
}

type PushReq struct {
	Client string                   `json:"client"`
	Files  []FileOp                 `json:"files"`
	Events []map[string]interface{} `json:"events"`
}

type PushResult struct {
	OpID   string `json:"op_id"`
	Status string `json:"status"` // applied | conflict | duplicate
	Seq    int64  `json:"seq"`
}

type Receipt struct {
	Seq     int64        `json:"seq"`
	Results []PushResult `json:"results"`
}

type Commit struct {
	Seq    int64                    `json:"seq"`
	Client string                   `json:"client"`
	OpIDs  []string                 `json:"op_ids"`
	Files  []FileOp                 `json:"files"`
	Events []map[string]interface{} `json:"events"`
}

type Entry struct {
	Status string `json:"status"` // alive | deleted
	Blob   string `json:"blob,omitempty"`
	Base   string `json:"base,omitempty"`
	Source string `json:"source,omitempty"`
	Seq    int64  `json:"seq"`
}

type Conflict struct {
	Path     string `json:"path"`
	Kind     string `json:"kind"` // fork | delete_intent
	Base     string `json:"base"`
	Current  string `json:"current,omitempty"`
	Incoming string `json:"incoming,omitempty"`
	Source   string `json:"source"`
	Seq      int64  `json:"seq"`
}

// ---------------------------------------------------------------------------
// 服务状态

type Server struct {
	mu        sync.Mutex
	dir       string
	head      int64
	opSeen    map[string]int64
	evSeen    map[string]int64
	files     map[string]*Entry
	conflicts []Conflict
	commits   []Commit
	// 故障注入
	dropNextReceipt bool
	crashAfterNext  bool
}

func NewServer(dir string) *Server {
	return &Server{
		dir:       dir,
		opSeen:    map[string]int64{},
		evSeen:    map[string]int64{},
		files:     map[string]*Entry{},
		conflicts: []Conflict{},
		commits:   []Commit{},
	}
}

func (s *Server) stateDir() string { return filepath.Join(s.dir, "run", "server-state") }
func (s *Server) blobDir() string  { return filepath.Join(s.stateDir(), "blobs") }

func blobID(content []byte) string {
	h := fnv.New64a()
	h.Write(content)
	return fmt.Sprintf("%016x", h.Sum64())
}

func (s *Server) load() error {
	os.MkdirAll(s.blobDir(), 0755)
	b, err := os.ReadFile(filepath.Join(s.stateDir(), "commits.jsonl"))
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var c Commit
		if err := json.Unmarshal([]byte(line), &c); err != nil {
			return fmt.Errorf("bad commit line: %v", err)
		}
		if c.Seq > s.head {
			s.head = c.Seq
		}
		for _, opID := range c.OpIDs {
			s.opSeen[opID] = c.Seq
		}
		for _, e := range c.Events {
			if id, _ := e["event_id"].(string); id != "" {
				s.evSeen[id] = c.Seq
			}
		}
		for i := range c.Files {
			s.applyOp(c.Files[i], true)
		}
		s.commits = append(s.commits, c)
	}
	return nil
}

func (s *Server) loadConflicts() error {
	b, err := os.ReadFile(filepath.Join(s.stateDir(), "conflicts.jsonl"))
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var c Conflict
		if err := json.Unmarshal([]byte(line), &c); err != nil {
			return err
		}
		s.conflicts = append(s.conflicts, c)
	}
	return nil
}

func (s *Server) appendConflict(c Conflict) {
	s.conflicts = append(s.conflicts, c)
	f, err := os.OpenFile(filepath.Join(s.stateDir(), "conflicts.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err == nil {
		enc := json.NewEncoder(f)
		enc.SetEscapeHTML(false)
		enc.Encode(c)
		f.Close()
	}
}

func (s *Server) readBlob(id string) ([]byte, error) {
	return os.ReadFile(filepath.Join(s.blobDir(), id))
}

func (s *Server) saveBlob(id string, content []byte) error {
	p := filepath.Join(s.blobDir(), id)
	if _, err := os.Stat(p); err == nil {
		return nil // 已存在
	}
	return os.WriteFile(p, content, 0644)
}

// 应用一个文件操作，只改内存 files。返回 status 与（冲突时）Conflict。
func (s *Server) applyOp(op FileOp, replay bool) (string, *Conflict) {
	e := s.files[op.Path]
	if op.Kind == "put" {
		if e == nil {
			s.files[op.Path] = &Entry{Status: "alive", Blob: op.Blob, Base: "", Source: op.OpID, Seq: s.head}
			return "applied", nil
		}
		if e.Status == "alive" {
			if e.Blob == op.Base {
				e.Blob = op.Blob
				e.Base = op.Base
				e.Source = op.OpID
				e.Seq = s.head
				return "applied", nil
			}
			return "conflict", &Conflict{Path: op.Path, Kind: "fork", Base: op.Base, Current: e.Blob, Incoming: op.Blob, Source: op.OpID, Seq: s.head}
		}
		// deleted
		if e.Base == op.Base {
			s.files[op.Path] = &Entry{Status: "alive", Blob: op.Blob, Base: op.Base, Source: op.OpID, Seq: s.head}
			return "applied", nil
		}
		return "conflict", &Conflict{Path: op.Path, Kind: "delete_intent", Base: e.Base, Incoming: op.Blob, Source: op.OpID, Seq: s.head}
	}
	// delete
	if e == nil {
		return "applied", nil
	}
	if e.Status == "deleted" {
		return "applied", nil
	}
	if e.Blob == op.Base {
		s.files[op.Path] = &Entry{Status: "deleted", Base: e.Blob, Source: op.OpID, Seq: s.head}
		return "applied", nil
	}
	return "conflict", &Conflict{Path: op.Path, Kind: "delete_intent", Base: op.Base, Current: e.Blob, Source: op.OpID, Seq: s.head}
}

func (s *Server) handlePush(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	var req PushReq
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, "bad json: "+err.Error(), 400)
		return
	}

	var newFiles []FileOp
	var results []PushResult
	for _, op := range req.Files {
		if seq, ok := s.opSeen[op.OpID]; ok {
			results = append(results, PushResult{OpID: op.OpID, Status: "duplicate", Seq: seq})
			continue
		}
		if op.Kind == "put" {
			raw, err := base64.StdEncoding.DecodeString(op.Content)
			if err != nil {
				http.Error(w, "bad content base64: "+err.Error(), 400)
				return
			}
			if op.Blob == "" {
				op.Blob = blobID(raw)
			} else if op.Blob != blobID(raw) {
				http.Error(w, "blob hash mismatch for "+op.Path, 400)
				return
			}
			if err := s.saveBlob(op.Blob, raw); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
		}
		newFiles = append(newFiles, op)
	}

	var newEvents []map[string]interface{}
	for _, e := range req.Events {
		id, _ := e["event_id"].(string)
		if id == "" {
			http.Error(w, "event missing event_id", 400)
			return
		}
		if _, ok := s.evSeen[id]; ok {
			continue
		}
		newEvents = append(newEvents, e)
	}

	if len(newFiles) == 0 && len(newEvents) == 0 {
		writeJSON(w, Receipt{Seq: s.head, Results: results})
		return
	}

	s.head++
	seq := s.head
	opIDs := []string{}
	for i := range newFiles {
		op := newFiles[i]
		opIDs = append(opIDs, op.OpID)
		st, cf := s.applyOp(op, false)
		s.opSeen[op.OpID] = seq
		if cf != nil {
			cf.Seq = seq
			s.appendConflict(*cf)
		}
		results = append(results, PushResult{OpID: op.OpID, Status: st, Seq: seq})
	}
	for _, e := range newEvents {
		id, _ := e["event_id"].(string)
		s.evSeen[id] = seq
	}

	commit := Commit{Seq: seq, Client: req.Client, OpIDs: opIDs, Files: newFiles, Events: newEvents}
	s.commits = append(s.commits, commit)
	if err := s.appendCommit(commit); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}

	resp := Receipt{Seq: seq, Results: results}

	// 故障注入：丢弃回执（提交已持久化，连接直接关闭，服务继续运行）
	if s.dropNextReceipt {
		s.dropNextReceipt = false
		if hj, ok := w.(http.Hijacker); ok {
			conn, _, _ := hj.Hijack()
			conn.Close()
		}
		return
	}
	// 故障注入：持久化后立即崩溃，不发送回执（模拟断电）
	if s.crashAfterNext {
		s.crashAfterNext = false
		os.Exit(3)
	}
	writeJSON(w, resp)
}

func (s *Server) appendCommit(c Commit) error {
	f, err := os.OpenFile(filepath.Join(s.stateDir(), "commits.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	b, _ := json.Marshal(c)
	if _, err := f.Write(append(b, '\n')); err != nil {
		return err
	}
	return f.Sync()
}

type PullResp struct {
	Head      int64                    `json:"head"`
	Manifest  map[string]string        `json:"manifest"`
	Blobs     map[string]string        `json:"blobs"`
	Deleted   map[string]string        `json:"deleted"`
	Conflicts []Conflict               `json:"conflicts"`
	Events    []map[string]interface{} `json:"events"`
}

func (s *Server) handlePull(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var after int64
	fmt.Sscanf(r.URL.Query().Get("after"), "%d", &after)

	resp := PullResp{
		Head:      s.head,
		Manifest:  map[string]string{},
		Blobs:     map[string]string{},
		Deleted:   map[string]string{},
		Conflicts: s.conflicts,
		Events:    []map[string]interface{}{},
	}
	for p, e := range s.files {
		if e.Status == "alive" {
			resp.Manifest[p] = e.Blob
			if _, ok := resp.Blobs[e.Blob]; !ok {
				if b, err := s.readBlob(e.Blob); err == nil {
					resp.Blobs[e.Blob] = base64.StdEncoding.EncodeToString(b)
				}
			}
		} else {
			resp.Deleted[p] = e.Base
		}
	}
	for _, c := range s.commits {
		if c.Seq > after {
			resp.Events = append(resp.Events, c.Events...)
		}
	}
	writeJSON(w, resp)
}

func (s *Server) handleFault(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch r.URL.Query().Get("kind") {
	case "drop":
		s.dropNextReceipt = true
		writeJSON(w, map[string]string{"armed": "drop"})
	case "crash":
		s.crashAfterNext = true
		writeJSON(w, map[string]string{"armed": "crash"})
	default:
		http.Error(w, "unknown fault kind", 400)
	}
}

func (s *Server) handleStats(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	writeJSON(w, map[string]interface{}{
		"head":      s.head,
		"commits":   len(s.commits),
		"files":     len(s.files),
		"conflicts": len(s.conflicts),
		"events":    len(s.evSeen),
	})
}

func writeJSON(w http.ResponseWriter, v interface{}) {
	b, _ := json.Marshal(v)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", fmt.Sprintf("%d", len(b)))
	w.WriteHeader(200)
	w.Write(b)
}

func main() {
	dir := flag.String("dir", ".", "工作目录（01-同步引擎）")
	port := flag.String("port", "8787", "监听端口")
	flag.Parse()
	abs, err := filepath.Abs(*dir)
	if err != nil {
		log.Fatal(err)
	}
	s := NewServer(abs)
	if err := s.load(); err != nil {
		log.Fatalf("load: %v", err)
	}
	if err := s.loadConflicts(); err != nil {
		log.Fatalf("load conflicts: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok")) })
	mux.HandleFunc("/push", s.handlePush)
	mux.HandleFunc("/pull", s.handlePull)
	mux.HandleFunc("/fault", s.handleFault)
	mux.HandleFunc("/stats", s.handleStats)

	addr := "127.0.0.1:" + *port
	log.Printf("sync server listening on http://%s state=%s", addr, s.stateDir())
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}
