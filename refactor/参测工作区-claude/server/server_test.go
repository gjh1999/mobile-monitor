package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testServer(t *testing.T) (*Server, *httptest.Server) {
	t.Helper()
	s, err := newServer(t.TempDir())
	if err != nil {
		t.Fatalf("newServer: %v", err)
	}
	ts := httptest.NewServer(s.routes())
	t.Cleanup(ts.Close)
	return s, ts
}

func postJSON(t *testing.T, url string, body any) (int, []byte, error) {
	t.Helper()
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	resp, err := http.Post(url, "application/json", bytes.NewReader(data))
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(resp.Body)
	return resp.StatusCode, out, err
}

func getJSON(t *testing.T, url string) map[string]any {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()
	var out map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode GET %s: %v", url, err)
	}
	return out
}

func pushBody(device, opID, path string, base uint64, content string) map[string]any {
	sum := sha256.Sum256([]byte(content))
	return map[string]any{
		"device_id": device,
		"op": map[string]any{
			"op_id":        opID,
			"path":         path,
			"base_version": base,
			"deleted":      false,
			"hash":         hex.EncodeToString(sum[:]),
			"content_b64":  base64.StdEncoding.EncodeToString([]byte(content)),
		},
	}
}

func TestPushIdempotentAndPull(t *testing.T) {
	_, ts := testServer(t)

	code, body, err := postJSON(t, ts.URL+"/api/push", pushBody("A", "A-1", "data/readings.txt", 0, "18\n22\n"))
	if err != nil {
		t.Fatalf("push: %v", err)
	}
	if code != 200 {
		t.Fatalf("push status %d: %s", code, body)
	}
	var applied map[string]any
	if err := json.Unmarshal(body, &applied); err != nil {
		t.Fatal(err)
	}
	if applied["result"] != "applied" || applied["version"].(float64) != 1 {
		t.Fatalf("unexpected ack: %v", applied)
	}

	// Replaying the same operation must not create a second logical commit.
	_, body, err = postJSON(t, ts.URL+"/api/push", pushBody("A", "A-1", "data/readings.txt", 0, "18\n22\n"))
	if err != nil {
		t.Fatalf("push replay: %v", err)
	}
	var replay map[string]any
	_ = json.Unmarshal(body, &replay)
	if replay["result"] != "already_applied" || replay["version"].(float64) != 1 {
		t.Fatalf("unexpected replay ack: %v", replay)
	}
	status := getJSON(t, ts.URL+"/api/status")
	if status["applied_ops"].(float64) != 1 {
		t.Fatalf("expected exactly one applied op, got %v", status["applied_ops"])
	}

	pulled := getJSON(t, ts.URL+"/api/pull?device_id=A&since=0")
	entries := pulled["entries"].([]any)
	if len(entries) != 1 {
		t.Fatalf("expected one pulled entry, got %v", entries)
	}
	e := entries[0].(map[string]any)
	raw, _ := base64.StdEncoding.DecodeString(e["content_b64"].(string))
	if string(raw) != "18\n22\n" {
		t.Fatalf("pulled bytes mismatch: %q", raw)
	}
}

func TestConflictRejected(t *testing.T) {
	s, ts := testServer(t)
	if _, _, err := postJSON(t, ts.URL+"/api/push", pushBody("A", "A-1", "f.txt", 0, "one\n")); err != nil {
		t.Fatal(err)
	}
	_, body, err := postJSON(t, ts.URL+"/api/push", pushBody("B", "B-1", "f.txt", 0, "two\n"))
	if err != nil {
		t.Fatal(err)
	}
	var resp map[string]any
	_ = json.Unmarshal(body, &resp)
	if resp["result"] != "conflict" {
		t.Fatalf("expected conflict, got %v", resp)
	}
	if len(s.state.Conflicts) != 1 {
		t.Fatalf("expected one conflict log, got %d", len(s.state.Conflicts))
	}
	if len(s.state.AppliedOps) != 1 {
		t.Fatalf("conflict must not apply; applied=%d", len(s.state.AppliedOps))
	}
	if s.state.Files["f.txt"].Hash == "" || len(s.state.Files) != 1 {
		t.Fatalf("server content changed")
	}
}

func TestFaultDropsSuccessResponseAndRetryDeduplicates(t *testing.T) {
	s, ts := testServer(t)

	if _, _, err := postJSON(t, ts.URL+"/api/admin/fault/drop-next-commit-ack",
		map[string]any{"enabled": true}); err != nil {
		t.Fatal(err)
	}

	// The client observes a transport failure even though the commit persisted.
	_, _, err := postJSON(t, ts.URL+"/api/push", pushBody("A", "A-fault", "notes/receipt.txt", 0, "sample-42\n"))
	if err == nil {
		t.Fatalf("expected dropped response (transport error)")
	}
	if len(s.state.AppliedOps) != 1 {
		t.Fatalf("commit should be persisted before drop; applied=%d", len(s.state.AppliedOps))
	}
	if s.state.FaultDropNextCommitAck {
		t.Fatalf("fault should be disarmed after firing")
	}
	if len(s.state.FaultLog) < 2 {
		t.Fatalf("expected arm+fired fault log, got %v", s.state.FaultLog)
	}

	// Retry with the same logical op is acknowledged without a second commit.
	_, body, err := postJSON(t, ts.URL+"/api/push", pushBody("A", "A-fault", "notes/receipt.txt", 0, "sample-42\n"))
	if err != nil {
		t.Fatal(err)
	}
	var resp map[string]any
	_ = json.Unmarshal(body, &resp)
	if resp["result"] != "already_applied" || resp["version"].(float64) != 1 {
		t.Fatalf("retry did not reuse original ack: %v", resp)
	}
	if len(s.state.AppliedOps) != 1 || s.state.NextVersion != 2 {
		t.Fatalf("expected one commit, got applied=%d next=%d", len(s.state.AppliedOps), s.state.NextVersion)
	}
}

func TestEventsDeduplicateByEventID(t *testing.T) {
	_, ts := testServer(t)
	ev1 := map[string]any{"event_id": "e1", "thread_id": "t", "turn_id": "u", "seq": 1,
		"type": "user.message", "item_id": "i1", "text": "hi"}
	ev2 := map[string]any{"event_id": "e2", "thread_id": "t", "turn_id": "u", "seq": 2,
		"type": "assistant.delta", "item_id": "i2", "text": "哈"}
	body := map[string]any{"device_id": "A", "events": []any{ev1, ev2}}
	_, out, err := postJSON(t, ts.URL+"/api/events", body)
	if err != nil {
		t.Fatal(err)
	}
	var resp map[string]any
	_ = json.Unmarshal(out, &resp)
	if resp["accepted"].(float64) != 2 || resp["total"].(float64) != 2 {
		t.Fatalf("unexpected first import: %v", resp)
	}
	_, out, err = postJSON(t, ts.URL+"/api/events", body)
	if err != nil {
		t.Fatal(err)
	}
	_ = json.Unmarshal(out, &resp)
	if resp["accepted"].(float64) != 0 || resp["duplicates"].(float64) != 2 {
		t.Fatalf("expected full dedupe, got %v", resp)
	}
	list := getJSON(t, ts.URL+"/api/events?thread_id=t")
	if len(list["events"].([]any)) != 2 {
		t.Fatalf("expected 2 events, got %v", list["events"])
	}
}

func TestInvalidPathRejected(t *testing.T) {
	_, ts := testServer(t)
	for _, p := range []string{"../evil", "C:/evil", "/abs"} {
		code, _, err := postJSON(t, ts.URL+"/api/push", pushBody("A", "op-"+p, p, 0, "x"))
		if err != nil {
			t.Fatal(err)
		}
		if code != 400 {
			t.Fatalf("path %q should be rejected, status=%d", p, code)
		}
	}
}
