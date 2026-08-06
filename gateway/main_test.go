package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestMaskSourceURLCredentialsWithAtSigns(t *testing.T) {
	raw := "rtsp://admin:Ssipdl2@@@10.0.2.15:554/cam/realmonitor?channel=2&subtype=1"

	masked := maskSourceURL(raw)

	if masked != "rtsp://admin:***@10.0.2.15:554/cam/realmonitor?channel=2&subtype=1" {
		t.Fatalf("unexpected masked URL: %s", masked)
	}
}

func TestMaskSourceURLPasswordQuery(t *testing.T) {
	raw := "rtsp://10.0.2.15/stream?user=admin&password=secret"

	masked := maskSourceURL(raw)

	if masked != "rtsp://10.0.2.15/stream?password=%2A%2A%2A&user=admin" {
		t.Fatalf("unexpected masked URL: %s", masked)
	}
}

func TestCameraListReturnsSortedSafeSummaries(t *testing.T) {
	gateway := &Gateway{cameras: map[int]*CameraRuntime{}, instanceID: "gateway-test"}
	gateway.cameras[42] = &CameraRuntime{state: CameraState{
		CameraID:  42,
		State:     stateRunning,
		SourceURL: "rtsp://admin:secret@10.0.0.42/live",
	}}
	gateway.cameras[41] = &CameraRuntime{state: CameraState{
		CameraID:  41,
		State:     stateQueued,
		SourceURL: "rtsp://10.0.0.41/live",
	}}

	request := httptest.NewRequest(http.MethodGet, "/cameras", nil)
	response := httptest.NewRecorder()
	gateway.cameraList(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", response.Code)
	}
	var payload struct {
		Count   int `json:"count"`
		Cameras []struct {
			CameraID         int  `json:"camera_id"`
			SourceRegistered bool `json:"source_registered"`
		} `json:"cameras"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatalf("invalid response: %v", err)
	}
	if payload.Count != 2 || payload.Cameras[0].CameraID != 41 || payload.Cameras[1].CameraID != 42 {
		t.Fatalf("unexpected camera order: %+v", payload.Cameras)
	}
	if !payload.Cameras[0].SourceRegistered || !payload.Cameras[1].SourceRegistered {
		t.Fatalf("registered sources were not reported: %+v", payload.Cameras)
	}
	responseBody := response.Body.String()
	if strings.Contains(responseBody, "source_url") || strings.Contains(responseBody, "secret") {
		t.Fatalf("source details leaked: %s", responseBody)
	}
}

func TestCircuitBreakerPublishesStableContractAndClosesAfterProbeFrame(t *testing.T) {
	gateway := &Gateway{
		cameras:        map[int]*CameraRuntime{},
		flapWindow:     2 * time.Minute,
		flapThreshold:  4,
		circuitBreaker: time.Minute,
		frameRingMax:   5,
		instanceID:     "gateway-test",
	}
	cam := &CameraRuntime{state: CameraState{CameraID: 41, State: stateReconnecting}, frameRingMax: 5}
	gateway.cameras[41] = cam
	now := time.Now().UTC()

	cam.mu.Lock()
	for i := 0; i < 3; i++ {
		if gateway.recordFlapLocked(cam, now.Add(time.Duration(i)*time.Second)) {
			t.Fatalf("circuit opened before threshold at flap %d", i+1)
		}
	}
	if !gateway.recordFlapLocked(cam, now.Add(3*time.Second)) {
		t.Fatal("circuit did not open at configured threshold")
	}
	cam.mu.Unlock()

	snapshot := gateway.cameraSnapshot(cam)
	if !snapshot.CircuitOpen || snapshot.CircuitState != "open" {
		t.Fatalf("unexpected open circuit snapshot: %+v", snapshot)
	}
	if snapshot.CircuitReason == "" || snapshot.CircuitOpenedAt == nil || snapshot.CircuitOpenUntil == nil {
		t.Fatalf("missing circuit contract fields: %+v", snapshot)
	}
	if snapshot.CircuitRetryAfter <= 0 || snapshot.GatewayInstanceID != "gateway-test" || snapshot.FailureEpoch != 1 {
		t.Fatalf("invalid circuit metadata: %+v", snapshot)
	}

	cam.mu.Lock()
	cam.circuitOpenUntil = now.Add(-time.Second)
	cam.circuitHalfOpen = true
	cam.mu.Unlock()
	if halfOpen := gateway.cameraSnapshot(cam); halfOpen.CircuitState != "half_open" || halfOpen.CircuitOpen {
		t.Fatalf("unexpected half-open snapshot: %+v", halfOpen)
	}

	gateway.recordFrame(cam, []byte{0xff, 0xd8, 0xff, 0xd9})
	closed := gateway.cameraSnapshot(cam)
	if closed.CircuitOpen || closed.CircuitState != "closed" || closed.CircuitReason != "" || closed.FlapCount != 0 {
		t.Fatalf("probe frame did not close and reset circuit: %+v", closed)
	}
	if closed.FailureEpoch != 1 {
		t.Fatalf("failure epoch must remain monotonic after close: %+v", closed)
	}
}

func TestCircuitContractIsIncludedInCameraListAndFrames(t *testing.T) {
	openedAt := time.Now().UTC().Add(-5 * time.Second)
	openUntil := time.Now().UTC().Add(time.Minute)
	cam := &CameraRuntime{
		state:            CameraState{CameraID: 41, State: stateUnstable, SourceURL: "rtsp://camera/41"},
		circuitOpenedAt:  openedAt,
		circuitOpenUntil: openUntil,
		circuitReason:    "4_reconnects_in_2m0s",
		failureEpoch:     2,
	}
	gateway := &Gateway{
		cameras:           map[int]*CameraRuntime{41: cam},
		instanceID:        "gateway-contract-test",
		defaultPriority:   5,
		queueAgingSeconds: 10,
	}

	for _, requestPath := range []string{"/cameras", "/cameras/41/frames"} {
		request := httptest.NewRequest(http.MethodGet, requestPath, nil)
		response := httptest.NewRecorder()
		if requestPath == "/cameras" {
			gateway.cameraList(response, request)
		} else {
			gateway.framesSince(response, request)
		}
		if response.Code != http.StatusOK {
			t.Fatalf("unexpected status for %s: %d", requestPath, response.Code)
		}
		body := response.Body.String()
		for _, field := range []string{"circuit_open", "circuit_state", "circuit_reason", "circuit_open_until", "retry_after_ms", "gateway_instance_id", "failure_epoch"} {
			if !strings.Contains(body, `"`+field+`"`) {
				t.Fatalf("%s missing from %s response: %s", field, requestPath, body)
			}
		}
	}
}
