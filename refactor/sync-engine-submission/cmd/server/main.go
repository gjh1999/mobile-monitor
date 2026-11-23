// Command server is the coordination service. Clients talk only to it;
// it never calls out. All state is durable before any ack is sent.
//
// Endpoints (JSON unless noted):
//
//	GET  /v1/status                     server counters + fault state
//	GET  /v1/log?limit=N                tail of the commit log (forensics)
//	PUT  /v1/blob/<sha256>              raw bytes; 409 on hash mismatch
//	GET  /v1/blob/<sha256>              raw bytes
//	POST /v1/commit                     idempotent by batch_id; fault point
//	GET  /v1/feed?since=&client=        changes after seq, excluding client
//	POST /v1/events                     idempotent by event_id; fault point
//	GET  /v1/events/feed?since=&client= events after seq, excluding client
//	POST /v1/admin/fault                arm/clear the single-shot ack drop
//	GET  /v1/admin/fault                current fault state
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"syncengine/internal/model"
	"syncengine/internal/store"
)

var startTime = time.Now()

func main() {
	addr := "127.0.0.1:8730"
	dir := "server-data"
	if len(os.Args) > 1 {
		dir = os.Args[1]
	}
	if len(os.Args) > 2 {
		addr = os.Args[2]
	}
	st, err := store.Open(dir)
	if err != nil {
		log.Fatalf("open store: %v", err)
	}
	defer st.Close()

	h := &handler{st: st}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/status", h.status)
	mux.HandleFunc("GET /v1/log", h.commitLog)
	mux.HandleFunc("PUT /v1/blob/", h.putBlob)
	mux.HandleFunc("GET /v1/blob/", h.getBlob)
	mux.HandleFunc("POST /v1/commit", h.commit)
	mux.HandleFunc("GET /v1/feed", h.feed)
	mux.HandleFunc("POST /v1/events", h.importEvents)
	mux.HandleFunc("GET /v1/events/feed", h.eventsFeed)
	mux.HandleFunc("POST /v1/admin/fault", h.setFault)
	mux.HandleFunc("GET /v1/admin/fault", h.getFault)

	log.Printf("coordinator listening on %s, state dir %s", addr, dir)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatalf("listen: %v", err)
	}
}

type handler struct{ st *store.Store }

func writeJSON(w http.ResponseWriter, code int, v any) {
	b, _ := json.Marshal(v)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", strconv.Itoa(len(b)))
	w.WriteHeader(code)
	w.Write(b)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func (h *handler) status(w http.ResponseWriter, r *http.Request) {
	m := h.st.Status()
	m["server_id"] = "coordinator"
	m["uptime_s"] = int64(time.Since(startTime).Seconds())
	m["now"] = time.Now().UTC().Format(time.RFC3339Nano)
	writeJSON(w, 200, m)
}

func (h *handler) commitLog(w http.ResponseWriter, r *http.Request) {
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 {
		limit = 50
	}
	writeJSON(w, 200, map[string]any{"commits": h.st.CommitLogTail(limit)})
}

func (h *handler) putBlob(w http.ResponseWriter, r *http.Request) {
	hash := strings.TrimPrefix(r.URL.Path, "/v1/blob/")
	if len(hash) != 64 {
		writeErr(w, 400, "bad blob hash")
		return
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 1<<28))
	if err != nil {
		writeErr(w, 400, "read body: "+err.Error())
		return
	}
	fresh, err := h.st.PutBlob(hash, data)
	if err != nil {
		writeErr(w, 409, err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"stored": fresh, "hash": hash, "size": len(data)})
}

func (h *handler) getBlob(w http.ResponseWriter, r *http.Request) {
	hash := strings.TrimPrefix(r.URL.Path, "/v1/blob/")
	data, err := h.st.GetBlob(hash)
	if err != nil {
		writeErr(w, 404, "blob not found")
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.WriteHeader(200)
	w.Write(data)
}

func (h *handler) commit(w http.ResponseWriter, r *http.Request) {
	var req model.CommitRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<24)).Decode(&req); err != nil {
		writeErr(w, 400, "bad json: "+err.Error())
		return
	}
	if req.BatchID == "" || req.Client == "" {
		writeErr(w, 400, "client and batch_id are required")
		return
	}
	for _, c := range req.Changes {
		if (c.Op == "upsert" || c.Op == "rename") && c.Hash != "" && !h.st.HasBlob(c.Hash) {
			writeErr(w, 412, "blob missing for "+c.Path)
			return
		}
	}
	// Durability first: the record is fsynced inside Commit. Only then may
	// we drop the ack under an armed fault, which is exactly the required
	// fault position: server persistently committed, sender gets no receipt.
	fresh, cseq, ver, err := h.st.Commit(&req)
	if err != nil {
		writeErr(w, 500, "commit failed: "+err.Error())
		return
	}
	if fresh && h.st.TakeFaultMatch("commit-ack", req.Client) {
		log.Printf("FAULT INJECTION: dropping commit ack for client=%s batch=%s (already durable, commit_seq=%d version=%d)",
			req.Client, req.BatchID, cseq, ver)
		dropResponse(w, r)
		return
	}
	writeJSON(w, 200, map[string]any{
		"committed":  boolInt(fresh),
		"duplicate":  !fresh,
		"commit_seq": cseq,
		"version":    ver,
	})
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// dropResponse hijacks the connection and closes it without writing any
// bytes, so the client sees an abrupt EOF: no response, no status code.
func dropResponse(w http.ResponseWriter, r *http.Request) {
	hj, ok := w.(http.Hijacker)
	if !ok {
		panic("cannot hijack")
	}
	conn, _, err := hj.Hijack()
	if err != nil {
		panic(err)
	}
	conn.Close() // abrupt: no HTTP response at all
}

func (h *handler) feed(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	since, _ := strconv.ParseInt(q.Get("since"), 10, 64)
	limit, _ := strconv.Atoi(q.Get("limit"))
	entries := h.st.Feed(since, q.Get("client"), limit)
	next := since
	for _, e := range entries {
		if e.ServerSeq > next {
			next = e.ServerSeq
		}
	}
	writeJSON(w, 200, map[string]any{"entries": entries, "next": next})
}

func (h *handler) importEvents(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Client  string        `json:"client"`
		BatchID string        `json:"batch_id"`
		Events  []model.Event `json:"events"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<24)).Decode(&req); err != nil {
		writeErr(w, 400, "bad json: "+err.Error())
		return
	}
	if req.BatchID == "" || req.Client == "" {
		writeErr(w, 400, "client and batch_id are required")
		return
	}
	for i := range req.Events {
		if err := validateEvent(&req.Events[i]); err != nil {
			writeErr(w, 422, fmt.Sprintf("event[%d]: %v", i, err))
			return
		}
	}
	accepted, dups, eseq, err := h.st.ImportEvents(req.Client, req.BatchID, req.Events)
	if err != nil {
		writeErr(w, 500, "import failed: "+err.Error())
		return
	}
	if h.st.TakeFaultMatch("events-ack", req.Client) {
		log.Printf("FAULT INJECTION: dropping events ack for client=%s batch=%s (already durable: accepted=%d dups=%d)",
			req.Client, req.BatchID, accepted, dups)
		dropResponse(w, r)
		return
	}
	writeJSON(w, 200, map[string]any{
		"accepted":   accepted,
		"duplicates": dups,
		"event_seq":  eseq,
	})
}

func validateEvent(e *model.Event) error {
	if e.EventID == "" || e.ThreadID == "" || e.TurnID == "" || e.ItemID == "" {
		return fmt.Errorf("event_id/thread_id/turn_id/item_id are required")
	}
	if e.Seq < 1 {
		return fmt.Errorf("seq must be >= 1")
	}
	switch e.Type {
	case "user.message":
		if e.Text == nil {
			return fmt.Errorf("user.message requires text")
		}
	case "tool.call":
		if e.CallID == "" || e.Name == "" || e.Args == nil {
			return fmt.Errorf("tool.call requires call_id, name, arguments")
		}
	case "tool.result":
		if e.CallID == "" || e.Result == nil {
			return fmt.Errorf("tool.result requires call_id and result")
		}
	case "assistant.delta":
		if e.Text == nil {
			return fmt.Errorf("assistant.delta requires text")
		}
	case "assistant.done":
	default:
		return fmt.Errorf("unknown type %q", e.Type)
	}
	return nil
}

func (h *handler) eventsFeed(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	since, _ := strconv.ParseInt(q.Get("since"), 10, 64)
	limit, _ := strconv.Atoi(q.Get("limit"))
	entries := h.st.EventsFeed(since, q.Get("client"), limit)
	next := since
	for _, e := range entries {
		if s, ok := e["event_seq"].(int64); ok && s > next {
			next = s
		}
	}
	writeJSON(w, 200, map[string]any{"entries": entries, "next": next})
}

func (h *handler) setFault(w http.ResponseWriter, r *http.Request) {
	var fs model.FaultSpec
	if err := json.NewDecoder(r.Body).Decode(&fs); err != nil {
		writeErr(w, 400, "bad json: "+err.Error())
		return
	}
	if fs.Armed {
		if fs.Mode != "commit-ack" && fs.Mode != "events-ack" {
			writeErr(w, 400, "mode must be commit-ack or events-ack")
			return
		}
		if fs.Target == "" {
			fs.Target = "any"
		}
		if fs.Count <= 0 {
			fs.Count = 1 // single-shot by default
		}
	}
	h.st.SetFault(fs)
	writeJSON(w, 200, map[string]any{"fault": h.st.Fault()})
}

func (h *handler) getFault(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"fault": h.st.Fault()})
}
