// Package store implements the durable state of the coordinator:
// an append-only commit log, an append-only event log, a content
// addressed blob store, and in-memory indexes rebuilt from the logs
// on startup. Every append is flushed to disk with fsync so that
// "persistently committed" is literally true before the ack is sent.
package store

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"syncengine/internal/model"
)

type BatchInfo struct {
	CommitSeq int64
	Version   int64
	Client    string
}

type EventInfo struct {
	EventSeq int64
	Client   string
}

type Store struct {
	mu       sync.Mutex
	dir      string
	blobDir  string

	commitsFile *os.File
	eventsFile  *os.File

	// counters and indexes
	commitSeq  int64 // last assigned commit log record number
	version    int64 // last assigned logical version (dups do not bump)
	serverSeq  int64 // last assigned per-change global sequence
	batchIndex map[string]BatchInfo

	eventSeq      int64 // last assigned global event sequence
	eventIndex    map[string]EventInfo
	eventOrder    []string // event_ids in arrival order

	commitCount int64 // real (non-duplicate) commits accepted
	dupHits     int64 // idempotent commit retries served from the index
	eventCount  int64 // unique events accepted
	eventDups   int64 // duplicate event deliveries ignored

	fault model.FaultSpec
}

func Open(dir string) (*Store, error) {
	s := &Store{
		dir:        dir,
		blobDir:    filepath.Join(dir, "blobs"),
		batchIndex: map[string]BatchInfo{},
		eventIndex: map[string]EventInfo{},
		fault:      model.FaultSpec{Armed: false},
	}
	if err := os.MkdirAll(s.blobDir, 0o755); err != nil {
		return nil, err
	}
	if err := s.replayLogs(); err != nil {
		return nil, err
	}
	cf, err := os.OpenFile(filepath.Join(dir, "commits.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, err
	}
	ef, err := os.OpenFile(filepath.Join(dir, "events.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		cf.Close()
		return nil, err
	}
	s.commitsFile = cf
	s.eventsFile = ef
	return s, nil
}

func (s *Store) Close() error {
	if s.commitsFile != nil {
		s.commitsFile.Close()
	}
	if s.eventsFile != nil {
		s.eventsFile.Close()
	}
	return nil
}

// replayLogs rebuilds all indexes by scanning the append-only logs.
// No snapshot file: logs are the single source of truth.
func (s *Store) replayLogs() error {
	f, err := os.Open(filepath.Join(s.dir, "commits.jsonl"))
	if err == nil {
		defer f.Close()
		dec := json.NewDecoder(f)
		for {
			var rec model.CommitRecord
			if err := dec.Decode(&rec); err != nil {
				break // EOF or a torn last line after a hard kill: stop here
			}
			s.commitSeq = rec.CommitSeq
			if rec.Duplicate {
				s.dupHits++
				continue
			}
			s.commitCount++
			if rec.Version > s.version {
				s.version = rec.Version
			}
			s.batchIndex[rec.BatchID] = BatchInfo{CommitSeq: rec.CommitSeq, Version: rec.Version, Client: rec.Client}
			for _, ch := range rec.Changes {
				s.serverSeq++ // per-change seq is positional; rebuilt deterministically
			}
		}
	}
	ef, err := os.Open(filepath.Join(s.dir, "events.jsonl"))
	if err == nil {
		defer ef.Close()
		dec := json.NewDecoder(ef)
		for {
			var rec model.EventLogRecord
			if err := dec.Decode(&rec); err != nil {
				break
			}
			s.eventSeq = rec.EventSeq
			if rec.Duplicate {
				s.eventDups++
				continue
			}
			s.eventCount++
			s.eventIndex[rec.Event.EventID] = EventInfo{EventSeq: rec.EventSeq, Client: rec.Client}
			s.eventOrder = append(s.eventOrder, rec.Event.EventID)
		}
	}
	return nil
}

func nowRFC() string { return time.Now().UTC().Format(time.RFC3339Nano) }

func (s *Store) appendJSON(f *os.File, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	b = append(b, '\n')
	if _, err := f.Write(b); err != nil {
		return err
	}
	return f.Sync() // durability before the ack leaves the server
}

// PutBlob stores raw bytes under their sha256; mismatching hash is rejected.
// Returns true if the blob was newly written, false if it already existed.
func (s *Store) PutBlob(hash string, data []byte) (bool, error) {
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != hash {
		return false, fmt.Errorf("blob hash mismatch: got %s want %s", hex.EncodeToString(sum[:]), hash)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	p := filepath.Join(s.blobDir, hash)
	if _, err := os.Stat(p); err == nil {
		return false, nil
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return false, err
	}
	if err := os.Rename(tmp, p); err != nil {
		return false, err
	}
	return true, nil
}

func (s *Store) GetBlob(hash string) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return os.ReadFile(filepath.Join(s.blobDir, hash))
}

func (s *Store) HasBlob(hash string) bool {
	_, err := os.Stat(filepath.Join(s.blobDir, hash))
	return err == nil
}

// Commit applies idempotency by BatchID, appends the record durably,
// and returns (newlyCommitted, commitSeq, version).
// The caller is responsible for dropping the ack when a fault is armed;
// by the time it does, the record is already on disk.
func (s *Store) Commit(req *model.CommitRequest) (bool, int64, int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if info, ok := s.batchIndex[req.BatchID]; ok {
		s.dupHits++
		rec := model.CommitRecord{
			CommitSeq: info.CommitSeq, Version: info.Version, Time: nowRFC(),
			Client: req.Client, BatchID: req.BatchID, Duplicate: true,
		}
		if err := s.appendJSON(s.commitsFile, rec); err != nil {
			return false, info.CommitSeq, info.Version, err
		}
		return false, info.CommitSeq, info.Version, nil
	}
	s.commitSeq++
	s.version++
	seq := s.commitSeq
	ver := s.version
	rec := model.CommitRecord{
		CommitSeq: seq, Version: ver, Time: nowRFC(),
		Client: req.Client, BatchID: req.BatchID, Changes: req.Changes,
	}
	if err := s.appendJSON(s.commitsFile, rec); err != nil {
		s.commitSeq--
		s.version--
		return false, 0, 0, err
	}
	s.batchIndex[req.BatchID] = BatchInfo{CommitSeq: seq, Version: ver, Client: req.Client}
	s.serverSeq += int64(len(req.Changes))
	s.commitCount++
	return true, seq, ver, nil
}

// ServerSeqFor returns the per-change sequence numbers assigned to a commit.
// Change i of commit with log record n occupies the n-th block of the
// positional counter; we recompute by scanning is too slow, so we record
// the starting seq in memory only. After restart the counter is rebuilt
// by replay, and feed pagination uses absolute seq numbers stored on
// each entry at read time.
func (s *Store) Feed(since int64, excludeClient string, limit int) []model.FeedEntry {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []model.FeedEntry{}
	// Walk the log from the beginning; the log is small in this deployment.
	// seq assignment: replayed from positional counting, so we recompute here.
	seq := int64(0)
	// To avoid re-reading the file we keep a read-through cache is overkill;
	// instead, read commits.jsonl directly (append-only, cheap at this scale).
	f, err := os.Open(filepath.Join(s.dir, "commits.jsonl"))
	if err != nil {
		return out
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	for {
		var rec model.CommitRecord
		if err := dec.Decode(&rec); err != nil {
			break
		}
		for i := range rec.Changes {
			seq++
			if rec.Duplicate {
				continue // duplicates carry no new changes; counter advances only for real commits
			}
			if seq <= since {
				continue
			}
			if excludeClient != "" && rec.Client == excludeClient {
				continue
			}
			out = append(out, model.FeedEntry{
				ServerSeq: seq, CommitSeq: rec.CommitSeq, Version: rec.Version,
				Client: rec.Client, BatchID: rec.BatchID, Change: rec.Changes[i],
			})
			if limit > 0 && len(out) >= limit {
				return out
			}
		}
	}
	return out
}

// ImportEvents dedups by event_id and appends durably.
func (s *Store) ImportEvents(client, batchID string, events []model.Event) (int, int, int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	accepted := 0
	dups := 0
	maxSeq := s.eventSeq
	for i := range events {
		ev := events[i]
		if _, ok := s.eventIndex[ev.EventID]; ok {
			dups++
			rec := model.EventLogRecord{
				EventSeq: s.eventIndex[ev.EventID].EventSeq, Time: nowRFC(),
				Client: client, BatchID: batchID, Event: ev, Duplicate: true,
			}
			if err := s.appendJSON(s.eventsFile, rec); err != nil {
				return accepted, dups, maxSeq, err
			}
			continue
		}
		s.eventSeq++
		maxSeq = s.eventSeq
		rec := model.EventLogRecord{
			EventSeq: s.eventSeq, Time: nowRFC(), Client: client, BatchID: batchID, Event: ev,
		}
		if err := s.appendJSON(s.eventsFile, rec); err != nil {
			s.eventSeq--
			return accepted, dups, maxSeq, err
		}
		s.eventIndex[ev.EventID] = EventInfo{EventSeq: s.eventSeq, Client: client}
		s.eventOrder = append(s.eventOrder, ev.EventID)
		accepted++
	}
	s.eventCount += int64(accepted)
	s.eventDups += int64(dups)
	return accepted, dups, maxSeq, nil
}

func (s *Store) EventsFeed(since int64, excludeClient string, limit int) []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []map[string]any{}
	f, err := os.Open(filepath.Join(s.dir, "events.jsonl"))
	if err != nil {
		return out
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	for {
		var rec model.EventLogRecord
		if err := dec.Decode(&rec); err != nil {
			break
		}
		if rec.Duplicate {
			continue
		}
		if rec.EventSeq <= since {
			continue
		}
		if excludeClient != "" && rec.Client == excludeClient {
			continue
		}
		out = append(out, map[string]any{
			"event_seq": rec.EventSeq,
			"client":    rec.Client,
			"event":     rec.Event,
		})
		if limit > 0 && len(out) >= limit {
			return out
		}
	}
	return out
}

func (s *Store) Status() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	return map[string]any{
		"version":        s.version,
		"commit_count":   s.commitCount,
		"dup_hits":       s.dupHits,
		"server_seq":     s.serverSeq,
		"event_count":    s.eventCount,
		"event_dup_hits": s.eventDups,
		"event_seq":      s.eventSeq,
		"unique_events":  len(s.eventIndex),
		"fault":          s.fault,
	}
}

func (s *Store) SetFault(fs model.FaultSpec) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.fault = fs
}

func (s *Store) Fault() model.FaultSpec {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.fault
}

// TakeFaultMatch reports whether a pending fault matches this request and,
// if so, consumes one use (single-shot semantics). Call it AFTER durable
// commit and BEFORE writing the response.
func (s *Store) TakeFaultMatch(mode, client string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.fault.Armed || s.fault.Count <= 0 || s.fault.Mode != mode {
		return false
	}
	if s.fault.Target != "any" && s.fault.Target != client {
		return false
	}
	s.fault.Count--
	if s.fault.Count <= 0 {
		s.fault.Armed = false
	}
	return true
}

// CommitLogTail returns up to limit most recent log lines for forensics.
func (s *Store) CommitLogTail(limit int) []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []map[string]any{}
	data, err := os.ReadFile(filepath.Join(s.dir, "commits.jsonl"))
	if err != nil {
		return out
	}
	lines := splitLines(data)
	if limit > 0 && len(lines) > limit {
		lines = lines[len(lines)-limit:]
	}
	for _, ln := range lines {
		var m map[string]any
		if json.Unmarshal(ln, &m) == nil {
			out = append(out, m)
		}
	}
	return out
}

func splitLines(data []byte) [][]byte {
	var out [][]byte
	start := 0
	for i, b := range data {
		if b == '\n' {
			if i > start {
				out = append(out, data[start:i])
			}
			start = i + 1
		}
	}
	if start < len(data) {
		out = append(out, data[start:])
	}
	return out
}
