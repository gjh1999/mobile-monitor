package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
)

const maxBodyBytes = 64 << 20

func writeJSON(w http.ResponseWriter, status int, v any) {
	data, err := json.Marshal(v)
	if err != nil {
		http.Error(w, `{"error":"encode response"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

func decodeJSONBody(r *http.Request, v any) error {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil {
		return fmt.Errorf("read body: %w", err)
	}
	if len(body) > maxBodyBytes {
		return fmt.Errorf("body exceeds %d bytes", maxBodyBytes)
	}
	if err := json.Unmarshal(body, v); err != nil {
		return fmt.Errorf("decode json: %w", err)
	}
	return nil
}

func parseUint(s string) (uint64, error) {
	if s == "" {
		return 0, nil
	}
	return strconv.ParseUint(s, 10, 64)
}
