package main

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"image/jpeg"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"
)

const (
	frameProtocolVersion = uint16(1)
	frameFileHeaderSize  = 128
	frameSlotHeaderSize  = 128
	framePayloadJPEG     = uint16(1)
	framePixelBGR24      = uint16(1)
	frameFlagReady       = uint16(1)
)

var frameFileMagic = [8]byte{'S', 'U', 'N', 'F', 'R', 'M', '0', '1'}

type frameTransportConfig struct {
	Mode         string
	Root         string
	CameraIDs    map[int]struct{}
	AllCameras   bool
	Protocol     uint16
	SlotCount    int
	SlotCapacity int
	RemoveOnStop bool
	FileMode     os.FileMode
}

func loadFrameTransportConfig() frameTransportConfig {
	mode := strings.ToLower(getenvDefault("FRAME_TRANSPORT_MODE", "http"))
	if mode != "http" && mode != "shared_memory_prefer" && mode != "shared_memory_strict" {
		transportLog.Warn("invalid_mode_using_http", "mode", mode)
		mode = "http"
	}
	protocol := getenvInt("FRAME_TRANSPORT_PROTOCOL_VERSION", int(frameProtocolVersion))
	if protocol <= 0 || protocol > 65535 {
		protocol = int(frameProtocolVersion)
	}
	ids, all := parseFrameTransportCameraIDs(os.Getenv("FRAME_TRANSPORT_CAMERA_IDS"))
	return frameTransportConfig{
		Mode:         mode,
		Root:         filepath.Clean(getenvDefault("FRAME_TRANSPORT_ROOT", "/run/sunorus/frames")),
		CameraIDs:    ids,
		AllCameras:   all,
		Protocol:     uint16(protocol),
		SlotCount:    clampInt(getenvInt("FRAME_TRANSPORT_SLOT_COUNT", 4), 2, 16),
		SlotCapacity: clampInt(getenvInt("FRAME_TRANSPORT_SLOT_CAPACITY_BYTES", 2*1024*1024), 64*1024, 32*1024*1024),
		RemoveOnStop: getenvBool("FRAME_TRANSPORT_REMOVE_ON_STOP", false),
		FileMode:     0o660,
	}
}

func parseFrameTransportCameraIDs(raw string) (map[int]struct{}, bool) {
	result := make(map[int]struct{})
	raw = strings.TrimSpace(raw)
	if raw == "*" {
		return result, true
	}
	for _, item := range strings.Split(raw, ",") {
		id, err := strconv.Atoi(strings.TrimSpace(item))
		if err == nil && id > 0 {
			result[id] = struct{}{}
		}
	}
	return result, false
}

func clampInt(value, minimum, maximum int) int {
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func (c frameTransportConfig) selected(cameraID int) bool {
	if c.Mode == "http" {
		return false
	}
	if c.AllCameras {
		return true
	}
	_, ok := c.CameraIDs[cameraID]
	return ok
}

func frameBufferPath(root string, cameraID int, protocol uint16) (string, error) {
	if cameraID <= 0 {
		return "", fmt.Errorf("invalid camera id")
	}
	if protocol == 0 {
		return "", fmt.Errorf("invalid protocol version")
	}
	name := fmt.Sprintf("camera_%d_v%d.mmap", cameraID, protocol)
	path := filepath.Join(filepath.Clean(root), name)
	if filepath.Dir(path) != filepath.Clean(root) {
		return "", fmt.Errorf("unsafe frame buffer path")
	}
	return path, nil
}

type sharedFrameMetrics struct {
	Ready             bool   `json:"shared_buffer_ready"`
	Mode              string `json:"frame_transport_mode"`
	Generation        uint64 `json:"shared_buffer_generation"`
	CapacityBytes     int    `json:"shared_buffer_capacity_bytes"`
	Slots             int    `json:"shared_buffer_slots"`
	FramesWritten     uint64 `json:"shared_buffer_frames_written_total"`
	FramesOverwritten uint64 `json:"shared_buffer_frames_overwritten_total"`
	WriteErrors       uint64 `json:"shared_buffer_write_errors_total"`
	PayloadTooLarge   uint64 `json:"shared_buffer_payload_too_large_total"`
	LastFrameID       uint64 `json:"shared_buffer_last_frame_id"`
	LastWriteAgeMS    int64  `json:"shared_buffer_last_write_age_ms"`
	HTTPFallbacks     uint64 `json:"frame_transport_http_fallback_total"`
	LastError         string `json:"frame_transport_error,omitempty"`
}

type sharedFrameBuffer struct {
	mu                sync.Mutex
	cameraID          int
	path              string
	config            frameTransportConfig
	region            *mappedRegion
	generation        uint64
	nextSlot          uint32
	framesWritten     uint64
	framesOverwritten uint64
	writeErrors       uint64
	payloadTooLarge   uint64
	lastFrameID       uint64
	lastWrite         time.Time
	active            bool
}

func newGenerationID() uint64 {
	var raw [8]byte
	if _, err := rand.Read(raw[:]); err == nil {
		value := binary.LittleEndian.Uint64(raw[:])
		if value != 0 {
			return value
		}
	}
	return uint64(time.Now().UnixNano()) ^ uint64(os.Getpid())
}

func createSharedFrameBuffer(config frameTransportConfig, cameraID int) (*sharedFrameBuffer, error) {
	if config.Protocol != frameProtocolVersion {
		return nil, fmt.Errorf("unsupported frame protocol version %d", config.Protocol)
	}
	path, err := frameBufferPath(config.Root, cameraID, config.Protocol)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(config.Root, 0o770); err != nil {
		return nil, fmt.Errorf("create frame transport root: %w", err)
	}
	totalSize := frameFileHeaderSize + config.SlotCount*(frameSlotHeaderSize+config.SlotCapacity)
	region, err := createMappedRegion(path, totalSize, config.FileMode)
	if err != nil {
		return nil, err
	}
	buffer := &sharedFrameBuffer{
		cameraID:   cameraID,
		path:       path,
		config:     config,
		region:     region,
		generation: newGenerationID(),
		active:     true,
	}
	buffer.initializeHeader()
	if err := region.flush(); err != nil {
		_ = region.close()
		return nil, err
	}
	return buffer, nil
}

func (b *sharedFrameBuffer) initializeHeader() {
	data := b.region.data
	for index := range data {
		data[index] = 0
	}
	copy(data[0:8], frameFileMagic[:])
	binary.LittleEndian.PutUint16(data[8:10], b.config.Protocol)
	binary.LittleEndian.PutUint16(data[10:12], frameFileHeaderSize)
	binary.LittleEndian.PutUint32(data[12:16], uint32(b.cameraID))
	binary.LittleEndian.PutUint32(data[16:20], uint32(b.config.SlotCount))
	binary.LittleEndian.PutUint32(data[20:24], uint32(b.config.SlotCapacity))
	binary.LittleEndian.PutUint64(data[24:32], b.generation)
	binary.LittleEndian.PutUint32(data[40:44], ^uint32(0))
	binary.LittleEndian.PutUint32(data[44:48], 1)
	binary.LittleEndian.PutUint64(data[48:56], uint64(time.Now().UnixNano()))
}

func atomicStoreUint64(data []byte, offset int, value uint64) {
	atomic.StoreUint64((*uint64)(unsafe.Pointer(&data[offset])), value)
}

func atomicStoreUint32(data []byte, offset int, value uint32) {
	atomic.StoreUint32((*uint32)(unsafe.Pointer(&data[offset])), value)
}

func (b *sharedFrameBuffer) writeJPEG(frameID uint64, capturedAt time.Time, payload []byte) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.region == nil || !b.active {
		b.writeErrors++
		return errors.New("shared frame buffer is not active")
	}
	if len(payload) == 0 {
		b.writeErrors++
		return errors.New("empty JPEG payload")
	}
	if len(payload) > b.config.SlotCapacity {
		b.payloadTooLarge++
		b.writeErrors++
		atomicStoreUint64(b.region.data, 88, b.payloadTooLarge)
		return fmt.Errorf("JPEG payload exceeds slot capacity")
	}
	imageConfig, err := jpeg.DecodeConfig(bytes.NewReader(payload))
	if err != nil || imageConfig.Width <= 0 || imageConfig.Height <= 0 {
		b.writeErrors++
		atomicStoreUint64(b.region.data, 80, b.writeErrors)
		return errors.New("invalid JPEG payload")
	}

	slot := b.nextSlot % uint32(b.config.SlotCount)
	slotBase := frameFileHeaderSize + int(slot)*(frameSlotHeaderSize+b.config.SlotCapacity)
	payloadBase := slotBase + frameSlotHeaderSize
	sequence := frameID * 2
	if sequence == 0 {
		sequence = 2
	}
	writingSequence := sequence - 1
	data := b.region.data

	atomicStoreUint64(data, slotBase, writingSequence)
	atomicStoreUint64(data, slotBase+8, writingSequence)
	binary.LittleEndian.PutUint64(data[slotBase+16:slotBase+24], b.generation)
	binary.LittleEndian.PutUint64(data[slotBase+24:slotBase+32], frameID)
	capturedMono := monotonicNowNS()
	publishedMono := monotonicNowNS()
	binary.LittleEndian.PutUint64(data[slotBase+32:slotBase+40], capturedMono)
	binary.LittleEndian.PutUint64(data[slotBase+40:slotBase+48], publishedMono)
	binary.LittleEndian.PutUint64(data[slotBase+48:slotBase+56], uint64(capturedAt.UnixNano()))
	binary.LittleEndian.PutUint32(data[slotBase+56:slotBase+60], uint32(imageConfig.Width))
	binary.LittleEndian.PutUint32(data[slotBase+60:slotBase+64], uint32(imageConfig.Height))
	binary.LittleEndian.PutUint16(data[slotBase+64:slotBase+66], 3)
	binary.LittleEndian.PutUint16(data[slotBase+66:slotBase+68], framePixelBGR24)
	binary.LittleEndian.PutUint16(data[slotBase+68:slotBase+70], framePayloadJPEG)
	binary.LittleEndian.PutUint16(data[slotBase+70:slotBase+72], frameFlagReady)
	binary.LittleEndian.PutUint32(data[slotBase+72:slotBase+76], uint32(len(payload)))
	binary.LittleEndian.PutUint32(data[slotBase+76:slotBase+80], uint32(b.config.SlotCapacity))
	binary.LittleEndian.PutUint32(data[slotBase+80:slotBase+84], crc32.ChecksumIEEE(payload))
	binary.LittleEndian.PutUint32(data[slotBase+84:slotBase+88], uint32(b.cameraID))
	binary.LittleEndian.PutUint32(data[slotBase+88:slotBase+92], slot)
	copy(data[payloadBase:payloadBase+len(payload)], payload)

	atomicStoreUint64(data, slotBase+8, sequence)
	atomicStoreUint64(data, slotBase, sequence)
	b.framesWritten++
	if b.framesWritten > uint64(b.config.SlotCount) {
		b.framesOverwritten++
	}
	b.lastFrameID = frameID
	b.lastWrite = time.Now()
	b.nextSlot = (slot + 1) % uint32(b.config.SlotCount)

	atomicStoreUint64(data, 24, b.generation)
	atomicStoreUint64(data, 32, frameID)
	atomicStoreUint32(data, 40, slot)
	atomicStoreUint32(data, 44, 1)
	atomicStoreUint64(data, 56, publishedMono)
	atomicStoreUint64(data, 64, b.framesWritten)
	atomicStoreUint64(data, 72, b.framesOverwritten)
	atomicStoreUint64(data, 80, b.writeErrors)
	atomicStoreUint64(data, 88, b.payloadTooLarge)
	return b.region.flush()
}

func (b *sharedFrameBuffer) markInactive() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.active = false
	if b.region != nil {
		atomicStoreUint32(b.region.data, 44, 0)
		_ = b.region.flush()
	}
}

func (b *sharedFrameBuffer) close(remove bool) {
	b.markInactive()
	b.mu.Lock()
	region := b.region
	b.region = nil
	path := b.path
	b.mu.Unlock()
	if region != nil {
		_ = region.close()
	}
	if remove {
		_ = os.Remove(path)
	}
}

func (b *sharedFrameBuffer) metrics(mode string) sharedFrameMetrics {
	b.mu.Lock()
	defer b.mu.Unlock()
	age := int64(0)
	if !b.lastWrite.IsZero() {
		age = time.Since(b.lastWrite).Milliseconds()
	}
	return sharedFrameMetrics{
		Ready:             b.active && b.region != nil,
		Mode:              mode,
		Generation:        b.generation,
		CapacityBytes:     b.config.SlotCapacity,
		Slots:             b.config.SlotCount,
		FramesWritten:     b.framesWritten,
		FramesOverwritten: b.framesOverwritten,
		WriteErrors:       b.writeErrors,
		PayloadTooLarge:   b.payloadTooLarge,
		LastFrameID:       b.lastFrameID,
		LastWriteAgeMS:    age,
	}
}

type frameTransportManager struct {
	mu         sync.RWMutex
	config     frameTransportConfig
	buffers    map[int]*sharedFrameBuffer
	fallbacks  map[int]uint64
	lastErrors map[int]string
	lastLogs   map[int]time.Time
}

func newFrameTransportManager(config frameTransportConfig) *frameTransportManager {
	return &frameTransportManager{
		config:     config,
		buffers:    make(map[int]*sharedFrameBuffer),
		fallbacks:  make(map[int]uint64),
		lastErrors: make(map[int]string),
		lastLogs:   make(map[int]time.Time),
	}
}

func (m *frameTransportManager) selected(cameraID int) bool {
	return m != nil && m.config.selected(cameraID)
}

func (m *frameTransportManager) reset(cameraID int) error {
	if !m.selected(cameraID) {
		return nil
	}
	buffer, err := createSharedFrameBuffer(m.config, cameraID)
	m.mu.Lock()
	old := m.buffers[cameraID]
	if err != nil {
		m.lastErrors[cameraID] = err.Error()
		m.mu.Unlock()
		return err
	}
	m.buffers[cameraID] = buffer
	delete(m.lastErrors, cameraID)
	m.mu.Unlock()
	if old != nil {
		old.close(false)
	}
	transportLog.Info("buffer_created",
		"cam", cameraID,
		"generation", buffer.generation,
		"slots", m.config.SlotCount,
		"capacity", m.config.SlotCapacity,
	)
	return nil
}

func (m *frameTransportManager) write(cameraID int, frameID uint64, capturedAt time.Time, jpegPayload []byte) error {
	if !m.selected(cameraID) {
		return nil
	}
	m.mu.RLock()
	buffer := m.buffers[cameraID]
	m.mu.RUnlock()
	if buffer == nil {
		if err := m.reset(cameraID); err != nil {
			return err
		}
		m.mu.RLock()
		buffer = m.buffers[cameraID]
		m.mu.RUnlock()
	}
	err := buffer.writeJPEG(frameID, capturedAt, jpegPayload)
	if err != nil {
		m.mu.Lock()
		m.lastErrors[cameraID] = err.Error()
		now := time.Now()
		if now.Sub(m.lastLogs[cameraID]) >= 5*time.Second {
			m.lastLogs[cameraID] = now
			transportLog.Error("frame_write_failed", "cam", cameraID, "mode", m.config.Mode, "error", err)
		}
		m.mu.Unlock()
	}
	return err
}

func (m *frameTransportManager) stop(cameraID int) {
	if m == nil {
		return
	}
	m.mu.RLock()
	buffer := m.buffers[cameraID]
	m.mu.RUnlock()
	if buffer != nil {
		buffer.markInactive()
	}
	if m.config.RemoveOnStop {
		m.remove(cameraID)
	}
}

func (m *frameTransportManager) remove(cameraID int) {
	m.mu.Lock()
	buffer := m.buffers[cameraID]
	delete(m.buffers, cameraID)
	delete(m.lastErrors, cameraID)
	m.mu.Unlock()
	if buffer != nil {
		buffer.close(true)
		transportLog.Info("buffer_removed", "cam", cameraID)
	}
}

func (m *frameTransportManager) metrics(cameraID int) sharedFrameMetrics {
	if m == nil || !m.selected(cameraID) {
		return sharedFrameMetrics{Mode: "http"}
	}
	m.mu.RLock()
	buffer := m.buffers[cameraID]
	fallbacks := m.fallbacks[cameraID]
	lastError := m.lastErrors[cameraID]
	mode := m.config.Mode
	m.mu.RUnlock()
	if buffer == nil {
		return sharedFrameMetrics{Mode: mode, HTTPFallbacks: fallbacks, LastError: lastError}
	}
	metrics := buffer.metrics(mode)
	metrics.HTTPFallbacks = fallbacks
	metrics.LastError = lastError
	return metrics
}

func (m *frameTransportManager) close() {
	if m == nil {
		return
	}
	m.mu.Lock()
	buffers := m.buffers
	m.buffers = make(map[int]*sharedFrameBuffer)
	m.mu.Unlock()
	for _, buffer := range buffers {
		buffer.close(false)
	}
}
