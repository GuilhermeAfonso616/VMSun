//go:build windows

package main

import (
	"fmt"
	"os"
)

// Windows development keeps HTTP as the supported runtime fallback. This
// file-backed implementation exists so protocol tests and local builds remain
// portable; Linux production uses MAP_SHARED in mapped_region_unix.go.
type mappedRegion struct {
	data []byte
	file *os.File
}

func createMappedRegion(path string, size int, mode os.FileMode) (*mappedRegion, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_RDWR, mode)
	if err != nil {
		return nil, fmt.Errorf("create frame buffer: %w", err)
	}
	if err := file.Truncate(int64(size)); err != nil {
		_ = file.Close()
		return nil, err
	}
	return &mappedRegion{data: make([]byte, size), file: file}, nil
}

func (m *mappedRegion) flush() error {
	if m.file == nil {
		return fmt.Errorf("frame buffer closed")
	}
	if _, err := m.file.WriteAt(m.data, 0); err != nil {
		return err
	}
	return m.file.Sync()
}

func (m *mappedRegion) close() error {
	m.data = nil
	if m.file == nil {
		return nil
	}
	err := m.file.Close()
	m.file = nil
	return err
}
