package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestMediaMTXOnlySourcePolicy(t *testing.T) {
	allowed := map[string]struct{}{"webrtc-gateway": {}, "10.0.0.5": {}}
	for _, source := range []string{"rtsp://webrtc-gateway:8554/cam_67", "rtsp://10.0.0.5:8554/cam_67"} {
		if err := validateGatewaySource("mediamtx_only", allowed, source); err != nil {
			t.Fatalf("expected accepted source %q: %v", source, err)
		}
	}
	for _, source := range []string{"rtsp://admin:secret@10.0.0.20/live", "rtsp://webrtc-gateway.evil.local/cam_67"} {
		if err := validateGatewaySource("mediamtx_only", allowed, source); err == nil {
			t.Fatalf("expected rejected source %q", source)
		}
	}
}

func TestSourcePolicyViolationDoesNotLeakCredentials(t *testing.T) {
	g := &Gateway{cameras: map[int]*CameraRuntime{}, sourcePolicy: "mediamtx_only", allowedRTSPHosts: map[string]struct{}{"webrtc-gateway": {}}, defaultPriority: 5}
	body := bytes.NewBufferString(`{"source_url":"rtsp://admin:secret@10.0.0.20/live"}`)
	request := httptest.NewRequest(http.MethodPost, "/cameras/67/source", body)
	response := httptest.NewRecorder()
	g.updateSource(response, request)
	if response.Code != http.StatusForbidden || strings.Contains(response.Body.String(), "secret") {
		t.Fatalf("unexpected policy response: %d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil || payload["error"] != "source_policy_violation" {
		t.Fatalf("unexpected payload: %v %v", payload, err)
	}
}

func TestInvalidSourcePolicyIsClear(t *testing.T) {
	if _, err := func() (string, error) {
		return "", validateGatewaySource("mediamtx_only", map[string]struct{}{}, "http://example")
	}(); err == nil {
		t.Fatal("invalid RTSP URL must be rejected")
	}
}

func TestBackboneRetryUsesShortDedicatedProfile(t *testing.T) {
	g := &Gateway{
		reconnectDelay:              5 * time.Second,
		reconnectBackoffMax:         30 * time.Second,
		backboneReconnectDelay:      time.Second,
		backboneReconnectBackoffMax: 5 * time.Second,
		allowedRTSPHosts:            map[string]struct{}{"webrtc-gateway": {}},
	}

	backboneDelay := g.retryDelayForSource(2, "rtsp://webrtc-gateway:8554/cam_36")
	directDelay := g.retryDelayForSource(2, "rtsp://192.0.2.10/stream")

	if backboneDelay > 5*time.Second {
		t.Fatalf("backbone retry exceeded dedicated cap: %s", backboneDelay)
	}
	if directDelay < 20*time.Second {
		t.Fatalf("direct camera retry unexpectedly shortened: %s", directDelay)
	}
}

func TestBackboneFailureDoesNotOpenPerCameraCircuit(t *testing.T) {
	g := &Gateway{
		allowedRTSPHosts: map[string]struct{}{"webrtc-gateway": {}},
		flapThreshold:    1,
		flapWindow:       time.Minute,
		circuitBreaker:   time.Minute,
		instanceID:       "test-gateway",
	}
	backboneCamera := &CameraRuntime{}
	directCamera := &CameraRuntime{}
	now := time.Now().UTC()

	if g.recordSourceFlapLocked(
		backboneCamera,
		"rtsp://webrtc-gateway:8554/cam_36",
		now,
	) {
		t.Fatal("managed backbone failure must not open a per-camera circuit")
	}
	if len(backboneCamera.flapEvents) != 0 {
		t.Fatalf("managed backbone failure was counted as a camera flap: %d", len(backboneCamera.flapEvents))
	}
	if !g.recordSourceFlapLocked(directCamera, "rtsp://192.0.2.10/stream", now) {
		t.Fatal("direct camera failure must keep the existing circuit breaker")
	}
}
