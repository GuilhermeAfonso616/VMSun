package main

import (
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

func gatewaySourcePolicy() (string, error) {
	policy := strings.ToLower(getenvDefault("GATEWAY_SOURCE_POLICY", "any"))
	if policy != "any" && policy != "mediamtx_only" {
		return "", fmt.Errorf("invalid GATEWAY_SOURCE_POLICY %q: expected any or mediamtx_only", policy)
	}
	return policy, nil
}

func gatewayAllowedRTSPHosts() map[string]struct{} {
	hosts := make(map[string]struct{})
	for _, raw := range strings.Split(getenvDefault("GATEWAY_ALLOWED_RTSP_HOSTS", "webrtc-gateway"), ",") {
		host := strings.ToLower(strings.TrimSpace(raw))
		if host != "" {
			hosts[host] = struct{}{}
		}
	}
	return hosts
}

func validateGatewaySource(policy string, allowed map[string]struct{}, source string) error {
	if policy == "" || policy == "any" {
		return nil
	}
	parsed, err := url.Parse(strings.TrimSpace(source))
	if err != nil || (strings.ToLower(parsed.Scheme) != "rtsp" && strings.ToLower(parsed.Scheme) != "rtsps") || parsed.Hostname() == "" {
		return fmt.Errorf("invalid RTSP source")
	}
	host := strings.ToLower(parsed.Hostname())
	if _, ok := allowed[host]; !ok {
		return fmt.Errorf("RTSP source host is not allowed")
	}
	return nil
}

func newGatewayInstanceID() string {
	return fmt.Sprintf("gateway-%d-%d", os.Getpid(), time.Now().UTC().UnixNano())
}

func getenvDefault(key, fallback string) string {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	return value
}

func getenvBool(key string, fallback bool) bool {
	value := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	if value == "" {
		return fallback
	}

	switch value {
	case "1", "true", "yes", "y", "on":
		return true
	case "0", "false", "no", "n", "off":
		return false
	default:
		return fallback
	}
}

func getenvDurationSeconds(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}

	seconds, err := strconv.ParseFloat(value, 64)
	if err != nil || seconds <= 0 {
		return fallback
	}

	return time.Duration(seconds * float64(time.Second))
}

func getenvDurationMillis(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}

	ms, err := strconv.ParseFloat(value, 64)
	if err != nil || ms <= 0 {
		return fallback
	}

	return time.Duration(ms * float64(time.Millisecond))
}

func getenvInt(key string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}

	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return fallback
	}

	return parsed
}

func getenvFloat(key string, fallback float64) float64 {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}

	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil || parsed <= 0 {
		return fallback
	}

	return parsed
}
