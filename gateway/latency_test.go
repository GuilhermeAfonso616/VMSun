package main

import (
	"bytes"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestParseLatencyProfiles(t *testing.T) {
	tests := []struct {
		value string
		want  ffmpegLatencyProfile
	}{
		{"", profileCompatibility},
		{"compatibility", profileCompatibility},
		{"balanced", profileBalanced},
		{"low_latency", profileLowLatency},
		{"low-latency", profileLowLatency},
	}
	for _, test := range tests {
		got, err := parseLatencyProfile(test.value)
		if err != nil || got != test.want {
			t.Fatalf("parseLatencyProfile(%q) = %q, %v; want %q", test.value, got, err, test.want)
		}
	}
	if _, err := parseLatencyProfile("turbo"); err == nil {
		t.Fatal("invalid profile was accepted")
	}
}

func TestCameraSelectorAndProfileFallback(t *testing.T) {
	selector := parseCameraSelector("37, 41,invalid")
	if !selector.selected(37) || !selector.selected(41) || selector.selected(42) {
		t.Fatalf("unexpected selector: %+v", selector)
	}
	if !parseCameraSelector("*").selected(999) {
		t.Fatal("wildcard did not select camera")
	}
	if parseCameraSelector("").selected(37) {
		t.Fatal("empty selector selected camera")
	}

	fallback := profileFallbackChain(profileLowLatency, true, false)
	want := []ffmpegLatencyProfile{profileLowLatency, profileBalanced, profileCompatibility}
	if len(fallback) != len(want) {
		t.Fatalf("unexpected fallback chain: %v", fallback)
	}
	for index := range want {
		if fallback[index] != want[index] {
			t.Fatalf("unexpected fallback chain: %v", fallback)
		}
	}
	if strict := profileFallbackChain(profileLowLatency, true, true); len(strict) != 1 || strict[0] != profileLowLatency {
		t.Fatalf("strict mode unexpectedly fell back: %v", strict)
	}
	if disabled := profileFallbackChain(profileBalanced, false, false); len(disabled) != 1 {
		t.Fatalf("disabled fallback unexpectedly expanded: %v", disabled)
	}
}

func TestProfileSelectionIsScopedPerCamera(t *testing.T) {
	gateway := &Gateway{
		ffmpegLatencyProfile:    profileCompatibility,
		ffmpegBalancedCameras:   parseCameraSelector("36"),
		ffmpegLowLatencyCameras: parseCameraSelector("37"),
	}
	if got := gateway.profileForCamera(35); got != profileCompatibility {
		t.Fatalf("unselected camera changed profile: %s", got)
	}
	if got := gateway.profileForCamera(36); got != profileBalanced {
		t.Fatalf("balanced canary was not selected: %s", got)
	}
	if got := gateway.profileForCamera(37); got != profileLowLatency {
		t.Fatalf("low-latency canary was not selected: %s", got)
	}
}

func TestFFmpegProfileArgumentsAndSanitization(t *testing.T) {
	if len(ffmpegInputArgs(profileCompatibility)) != 0 {
		t.Fatal("compatibility profile must preserve the previous input behavior")
	}
	balanced := strings.Join(ffmpegInputArgs(profileBalanced), " ")
	if !strings.Contains(balanced, "discardcorrupt") || strings.Contains(balanced, "nobuffer") {
		t.Fatalf("unexpected balanced arguments: %s", balanced)
	}
	low := strings.Join(ffmpegInputArgs(profileLowLatency), " ")
	for _, expected := range []string{"nobuffer", "low_delay", "max_delay"} {
		if !strings.Contains(low, expected) {
			t.Fatalf("low-latency arguments missing %q: %s", expected, low)
		}
	}

	command := sanitizedFFmpegCommand(
		"ffmpeg",
		[]string{"-rtsp_transport", "tcp", "-i", "rtsp://admin:secret@10.0.0.37/live", "-f", "null", "-"},
	)
	if strings.Contains(command, "secret") || !strings.Contains(command, "***") {
		t.Fatalf("credentials leaked in command: %s", command)
	}
}

func TestLatestFrameMailboxKeepsOnlyNewest(t *testing.T) {
	mailbox := newLatestFrameMailbox()
	if replaced, ok := mailbox.Put(gatewayFrame{JPEG: []byte{1}}); replaced || !ok {
		t.Fatalf("unexpected first put: replaced=%v ok=%v", replaced, ok)
	}
	if replaced, ok := mailbox.Put(gatewayFrame{JPEG: []byte{2}}); !replaced || !ok {
		t.Fatalf("pending frame was not replaced: replaced=%v ok=%v", replaced, ok)
	}
	if mailbox.Len() != 1 || mailbox.Replacements() != 1 {
		t.Fatalf("mailbox grew or replacement was not counted: len=%d replacements=%d", mailbox.Len(), mailbox.Replacements())
	}
	frame := <-mailbox.Frames()
	if len(frame.JPEG) != 1 || frame.JPEG[0] != 2 {
		t.Fatalf("mailbox returned stale frame: %v", frame.JPEG)
	}
	mailbox.Close()
	if _, ok := mailbox.Put(gatewayFrame{JPEG: []byte{3}}); ok {
		t.Fatal("closed mailbox accepted frame")
	}
}

func TestLatestFrameMailboxConcurrentProducerDoesNotGrow(t *testing.T) {
	mailbox := newLatestFrameMailbox()
	var producers sync.WaitGroup
	for producer := 0; producer < 4; producer++ {
		producers.Add(1)
		go func(seed byte) {
			defer producers.Done()
			for index := 0; index < 500; index++ {
				_, _ = mailbox.Put(gatewayFrame{JPEG: []byte{seed, byte(index)}})
			}
		}(byte(producer))
	}
	producers.Wait()
	if mailbox.Len() > 1 {
		t.Fatalf("logical capacity exceeded: %d", mailbox.Len())
	}
	if mailbox.Replacements() == 0 {
		t.Fatal("fast producers did not replace pending frames")
	}
	mailbox.Close()
}

func TestMJPEGReaderWithSlowConsumerKeepsLatestFrame(t *testing.T) {
	mailbox := newLatestFrameMailbox()
	stream := bytes.NewReader([]byte{
		0xff, 0xd8, 1, 0xff, 0xd9,
		0xff, 0xd8, 2, 0xff, 0xd9,
		0xff, 0xd8, 3, 0xff, 0xd9,
	})
	var replaced int
	if err := readMJPEGFrames(stream, mailbox, func(wasReplaced bool, _ int, _ gatewayFrame) {
		if wasReplaced {
			replaced++
		}
	}); err != nil {
		t.Fatalf("readMJPEGFrames failed: %v", err)
	}
	if mailbox.Len() != 1 || replaced != 2 {
		t.Fatalf("slow consumer accumulated frames: len=%d replaced=%d", mailbox.Len(), replaced)
	}
	frame := <-mailbox.Frames()
	if !bytes.Equal(frame.JPEG, []byte{0xff, 0xd8, 3, 0xff, 0xd9}) {
		t.Fatalf("latest frame was not retained: %v", frame.JPEG)
	}
}

func TestLatestFrameMailboxSlowConsumerDoesNotReplayBacklog(t *testing.T) {
	mailbox := newLatestFrameMailbox()
	consumed := make(chan byte, 8)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for frame := range mailbox.Frames() {
			consumed <- frame.JPEG[0]
			time.Sleep(10 * time.Millisecond)
		}
	}()

	for value := byte(1); value <= 40; value++ {
		_, ok := mailbox.Put(gatewayFrame{JPEG: []byte{value}})
		if !ok {
			t.Fatalf("mailbox rejected frame %d", value)
		}
		time.Sleep(time.Millisecond)
	}
	time.Sleep(25 * time.Millisecond)
	mailbox.Close()
	<-done
	close(consumed)

	values := make([]byte, 0, len(consumed))
	for value := range consumed {
		values = append(values, value)
	}
	if len(values) >= 20 {
		t.Fatalf("slow consumer replayed a backlog: consumed=%d values=%v", len(values), values)
	}
	if values[len(values)-1] != 40 {
		t.Fatalf("slow consumer did not receive newest frame: %v", values)
	}
	if mailbox.Replacements() == 0 {
		t.Fatal("slow consumer did not cause replacements")
	}
}

func TestPTSDriftAndDiscontinuity(t *testing.T) {
	var estimator ptsDriftEstimator
	base := time.Unix(100, 0)
	first := estimator.Observe(10*time.Second, base)
	if !first.Available || first.Drift != 0 {
		t.Fatalf("unexpected PTS base: %+v", first)
	}
	zero := estimator.Observe(11*time.Second, base.Add(time.Second))
	if zero.Drift != 0 {
		t.Fatalf("real-time PTS generated drift: %s", zero.Drift)
	}
	lag := estimator.Observe(11*time.Second+200*time.Millisecond, base.Add(2*time.Second))
	if lag.Drift != 800*time.Millisecond {
		t.Fatalf("unexpected increasing drift: %s", lag.Drift)
	}
	regression := estimator.Observe(5*time.Second, base.Add(3*time.Second))
	if !regression.Discontinuity || regression.Drift != 0 || regression.Discontinuities != 1 {
		t.Fatalf("PTS regression was not rebased: %+v", regression)
	}
	if unavailable := estimator.Observe(-time.Second, base); unavailable.Available {
		t.Fatalf("invalid PTS was accepted: %+v", unavailable)
	}
}

func TestLagWatchdogRequiresSustainedLagAndHonorsCooldown(t *testing.T) {
	watchdog := newLagWatchdog(true, 2*time.Second, 5*time.Second, 30*time.Second, 2)
	base := time.Unix(100, 0)
	if watchdog.Observe(base, 3*time.Second, true) {
		t.Fatal("watchdog restarted on first lag sample")
	}
	if watchdog.Observe(base.Add(4*time.Second), 3*time.Second, true) {
		t.Fatal("watchdog restarted before hold time")
	}
	if !watchdog.Observe(base.Add(5*time.Second), 3*time.Second, true) {
		t.Fatal("sustained lag did not trigger restart")
	}
	if watchdog.Observe(base.Add(6*time.Second), 3*time.Second, true) {
		t.Fatal("watchdog ignored cooldown")
	}
	if watchdog.Observe(base.Add(40*time.Second), time.Second, true) {
		t.Fatal("healthy observation triggered restart")
	}
	if watchdog.State() != "healthy" {
		t.Fatalf("watchdog did not recover: %s", watchdog.State())
	}
	disabled := newLagWatchdog(false, time.Second, time.Second, time.Second, 1)
	if disabled.Observe(base, 10*time.Second, true) || disabled.State() != "disabled" {
		t.Fatalf("disabled watchdog acted: state=%s", disabled.State())
	}
}

func TestEstimatedStaleFrameIsDroppedOnlyForSelectedCamera(t *testing.T) {
	gateway := &Gateway{
		frameRingMax:               1,
		instanceID:                 "gateway-test",
		maxAnalyticFrameAge:        500 * time.Millisecond,
		maxAnalyticFrameAgeCameras: parseCameraSelector("37"),
	}
	stale := frameObservation{
		receivedAt: time.Now(),
		pts: ptsObservation{
			Available: true,
			PTS:       time.Second,
			Drift:     700 * time.Millisecond,
		},
	}
	selected := &CameraRuntime{
		state:        CameraState{CameraID: 37},
		frameRingMax: 1,
	}
	if gateway.recordFrameObserved(selected, []byte{1}, stale) {
		t.Fatal("selected stale frame was published")
	}
	if selected.latestSeq != 0 || selected.state.GatewayFramesStaleTotal != 1 {
		t.Fatalf("stale drop was not recorded: %+v", selected.state)
	}

	unselected := &CameraRuntime{
		state:        CameraState{CameraID: 38},
		frameRingMax: 1,
	}
	if !gateway.recordFrameObserved(unselected, []byte{2}, stale) || unselected.latestSeq != 1 {
		t.Fatal("unselected camera was affected by canary age limit")
	}
}

func TestStreamGenerationChangesAcrossRestart(t *testing.T) {
	gateway := &Gateway{instanceID: "gateway-test"}
	before := gateway.streamGenerationID(37, 8)
	after := gateway.streamGenerationID(37, 9)
	if before == after || !strings.Contains(after, "cam37-gen9") {
		t.Fatalf("stream generation did not change: before=%q after=%q", before, after)
	}
}

func TestReadFFmpegProgressExtractsPTSWithoutReturningProgressNoise(t *testing.T) {
	var observations []time.Duration
	warnings := readFFmpegProgress(
		strings.NewReader("frame=1\nout_time_us=250000\nprogress=continue\nwarning text\nout_time_us=500000\n"),
		func(value time.Duration) { observations = append(observations, value) },
	)
	if len(observations) != 2 || observations[0] != 250*time.Millisecond || observations[1] != 500*time.Millisecond {
		t.Fatalf("unexpected PTS observations: %v", observations)
	}
	if string(warnings) != "warning text" {
		t.Fatalf("unexpected warning capture: %q", warnings)
	}
}
