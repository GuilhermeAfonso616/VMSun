package main

import (
	"bytes"
	"encoding/binary"
	"image"
	"image/color"
	"image/jpeg"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testJPEG(t *testing.T, width, height int) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, width, height))
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			img.Set(x, y, color.RGBA{R: uint8(x), G: uint8(y), B: 90, A: 255})
		}
	}
	var output bytes.Buffer
	if err := jpeg.Encode(&output, img, &jpeg.Options{Quality: 75}); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

func testFrameConfig(root string) frameTransportConfig {
	return frameTransportConfig{
		Mode:         "shared_memory_strict",
		Root:         root,
		CameraIDs:    map[int]struct{}{36: {}},
		Protocol:     frameProtocolVersion,
		SlotCount:    4,
		SlotCapacity: 128 * 1024,
		FileMode:     0o660,
	}
}

func TestFrameTransportSelection(t *testing.T) {
	ids, all := parseFrameTransportCameraIDs("36, 37,invalid")
	if all || len(ids) != 2 {
		t.Fatalf("unexpected selection: all=%v ids=%v", all, ids)
	}
	_, all = parseFrameTransportCameraIDs("*")
	if !all {
		t.Fatal("wildcard should select all cameras")
	}
	config := testFrameConfig(t.TempDir())
	if !config.selected(36) || config.selected(35) {
		t.Fatal("camera selection mismatch")
	}
	config.Mode = "http"
	if config.selected(36) {
		t.Fatal("http mode must ignore camera selection")
	}
}

func TestFrameBufferNameIsSafeAndVersioned(t *testing.T) {
	path, err := frameBufferPath("/run/sunorus/frames", 36, 1)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(path) != "camera_36_v1.mmap" {
		t.Fatalf("unexpected path: %s", path)
	}
	for _, invalidID := range []int{0, -1} {
		if _, err := frameBufferPath(t.TempDir(), invalidID, 1); err == nil {
			t.Fatalf("camera id %d should be rejected", invalidID)
		}
	}
}

func TestSharedFrameBufferHeaderAndLittleEndian(t *testing.T) {
	buffer, err := createSharedFrameBuffer(testFrameConfig(t.TempDir()), 36)
	if err != nil {
		t.Fatal(err)
	}
	defer buffer.close(true)
	data := buffer.region.data
	if !bytes.Equal(data[0:8], frameFileMagic[:]) {
		t.Fatal("invalid magic")
	}
	if got := binary.LittleEndian.Uint16(data[8:10]); got != 1 {
		t.Fatalf("unexpected version %d", got)
	}
	if got := binary.LittleEndian.Uint32(data[12:16]); got != 36 {
		t.Fatalf("unexpected camera id %d", got)
	}
	if got := binary.LittleEndian.Uint32(data[16:20]); got != 4 {
		t.Fatalf("unexpected slot count %d", got)
	}
}

func TestSharedFrameBufferWritesAndRotatesSlots(t *testing.T) {
	buffer, err := createSharedFrameBuffer(testFrameConfig(t.TempDir()), 36)
	if err != nil {
		t.Fatal(err)
	}
	defer buffer.close(true)
	payload := testJPEG(t, 64, 48)
	for frameID := uint64(1); frameID <= 6; frameID++ {
		if err := buffer.writeJPEG(frameID, time.Now(), payload); err != nil {
			t.Fatal(err)
		}
	}
	data := buffer.region.data
	if latest := binary.LittleEndian.Uint64(data[32:40]); latest != 6 {
		t.Fatalf("unexpected latest frame %d", latest)
	}
	if slot := binary.LittleEndian.Uint32(data[40:44]); slot != 1 {
		t.Fatalf("unexpected latest slot %d", slot)
	}
	metrics := buffer.metrics("shared_memory_strict")
	if metrics.FramesWritten != 6 || metrics.FramesOverwritten != 2 {
		t.Fatalf("unexpected metrics: %+v", metrics)
	}
	slotBase := frameFileHeaderSize + int(1)*(frameSlotHeaderSize+buffer.config.SlotCapacity)
	begin := binary.LittleEndian.Uint64(data[slotBase : slotBase+8])
	end := binary.LittleEndian.Uint64(data[slotBase+8 : slotBase+16])
	if begin == 0 || begin != end || begin&1 != 0 {
		t.Fatalf("slot was not committed atomically: begin=%d end=%d", begin, end)
	}
	if got := binary.LittleEndian.Uint64(data[slotBase+24 : slotBase+32]); got != 6 {
		t.Fatalf("unexpected slot frame id %d", got)
	}
}

func TestSharedFrameBufferRejectsEmptyInvalidAndOversizedPayload(t *testing.T) {
	config := testFrameConfig(t.TempDir())
	config.SlotCapacity = 64 * 1024
	buffer, err := createSharedFrameBuffer(config, 36)
	if err != nil {
		t.Fatal(err)
	}
	defer buffer.close(true)
	if err := buffer.writeJPEG(1, time.Now(), nil); err == nil {
		t.Fatal("empty payload should fail")
	}
	if err := buffer.writeJPEG(2, time.Now(), []byte("not-a-jpeg")); err == nil {
		t.Fatal("invalid jpeg should fail")
	}
	if err := buffer.writeJPEG(3, time.Now(), make([]byte, config.SlotCapacity+1)); err == nil {
		t.Fatal("oversized payload should fail")
	}
	metrics := buffer.metrics("shared_memory_strict")
	if metrics.WriteErrors != 3 || metrics.PayloadTooLarge != 1 {
		t.Fatalf("unexpected error metrics: %+v", metrics)
	}
}

func TestFrameBufferGenerationChangesOnRecreation(t *testing.T) {
	config := testFrameConfig(t.TempDir())
	first, err := createSharedFrameBuffer(config, 36)
	if err != nil {
		t.Fatal(err)
	}
	firstGeneration := first.generation
	first.close(false)
	second, err := createSharedFrameBuffer(config, 36)
	if err != nil {
		t.Fatal(err)
	}
	defer second.close(true)
	if second.generation == 0 || second.generation == firstGeneration {
		t.Fatalf("generation did not change: %d", second.generation)
	}
}

func TestFrameTransportManagerStopAndRemoveAreCameraScoped(t *testing.T) {
	config := testFrameConfig(t.TempDir())
	config.CameraIDs[37] = struct{}{}
	manager := newFrameTransportManager(config)
	if err := manager.reset(36); err != nil {
		t.Fatal(err)
	}
	if err := manager.reset(37); err != nil {
		t.Fatal(err)
	}
	manager.stop(36)
	if manager.metrics(36).Ready {
		t.Fatal("stopped camera buffer should be inactive")
	}
	if !manager.metrics(37).Ready {
		t.Fatal("stopping camera 36 affected camera 37")
	}
	manager.remove(36)
	path, _ := frameBufferPath(config.Root, 36, config.Protocol)
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("camera 36 resource still exists: %v", err)
	}
	if !manager.metrics(37).Ready {
		t.Fatal("removing camera 36 affected camera 37")
	}
	manager.close()
}

func TestFrameTransportFilesystemFailureIsExplicit(t *testing.T) {
	parent := t.TempDir()
	rootFile := filepath.Join(parent, "not-a-directory")
	if err := os.WriteFile(rootFile, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	config := testFrameConfig(rootFile)
	if _, err := createSharedFrameBuffer(config, 36); err == nil {
		t.Fatal("filesystem failure should be returned")
	}
}
