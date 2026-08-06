package main

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

func parseUint64Query(value string, fallback uint64) uint64 {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return fallback
	}

	parsed, err := strconv.ParseUint(trimmed, 10, 64)
	if err != nil {
		return fallback
	}

	return parsed
}

func parseIntQuery(value string, fallback int) int {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return fallback
	}

	parsed, err := strconv.Atoi(trimmed)
	if err != nil {
		return fallback
	}

	return parsed
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func maskSourceURL(rawURL string) string {
	value := strings.TrimSpace(rawURL)
	if value == "" {
		return ""
	}

	masked := value
	if schemeIndex := strings.Index(masked, "://"); schemeIndex >= 0 {
		authorityStart := schemeIndex + len("://")
		authorityEnd := len(masked)
		for _, separator := range []string{"/", "?", "#"} {
			if idx := strings.Index(masked[authorityStart:], separator); idx >= 0 && authorityStart+idx < authorityEnd {
				authorityEnd = authorityStart + idx
			}
		}
		if atIndex := strings.LastIndex(masked[authorityStart:authorityEnd], "@"); atIndex >= 0 {
			credentialsStart := authorityStart
			credentialsEnd := authorityStart + atIndex
			credentials := masked[credentialsStart:credentialsEnd]
			username := credentials
			if colonIndex := strings.Index(credentials, ":"); colonIndex >= 0 {
				username = credentials[:colonIndex]
			}
			replacement := "***"
			if username != "" {
				replacement = username + ":***"
			}
			masked = masked[:credentialsStart] + replacement + masked[credentialsEnd:]
		}
	}

	parsed, err := url.Parse(masked)
	if err != nil || parsed.RawQuery == "" {
		return masked
	}

	query := parsed.Query()
	changed := false
	for _, key := range []string{"password", "passwd", "pwd", "pass"} {
		if _, ok := query[key]; ok {
			query.Set(key, "***")
			changed = true
		}
	}
	if !changed {
		return masked
	}

	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func parseCameraID(path string) (int, bool) {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) < 2 || parts[0] != "cameras" {
		return 0, false
	}

	id, err := strconv.Atoi(parts[1])
	if err != nil || id <= 0 {
		return 0, false
	}

	return id, true
}

func utcPtr(t time.Time) *time.Time {
	value := t.UTC()
	return &value
}

func durationMs(start, end time.Time) int64 {
	if start.IsZero() || end.IsZero() || end.Before(start) {
		return 0
	}
	return end.Sub(start).Milliseconds()
}

func cloneBytes(value []byte) []byte {
	if len(value) == 0 {
		return nil
	}

	copyValue := make([]byte, len(value))
	copy(copyValue, value)
	return copyValue
}
