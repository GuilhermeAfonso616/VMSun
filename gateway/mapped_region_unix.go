//go:build !windows

package main

import (
	"fmt"
	"os"
	"syscall"
)

type mappedRegion struct {
	data []byte
	file *os.File
}

func createMappedRegion(path string, size int, mode os.FileMode) (*mappedRegion, error) {
	temp, err := os.CreateTemp(filepathDir(path), ".frame-buffer-*")
	if err != nil {
		return nil, fmt.Errorf("create frame buffer: %w", err)
	}
	tempPath := temp.Name()
	cleanup := func() {
		_ = temp.Close()
		_ = os.Remove(tempPath)
	}
	if err := temp.Chmod(mode); err != nil {
		cleanup()
		return nil, err
	}
	if err := temp.Truncate(int64(size)); err != nil {
		cleanup()
		return nil, err
	}
	data, err := syscall.Mmap(int(temp.Fd()), 0, size, syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	if err != nil {
		cleanup()
		return nil, fmt.Errorf("mmap frame buffer: %w", err)
	}
	if err := os.Rename(tempPath, path); err != nil {
		_ = syscall.Munmap(data)
		cleanup()
		return nil, fmt.Errorf("publish frame buffer: %w", err)
	}
	return &mappedRegion{data: data, file: temp}, nil
}

func filepathDir(path string) string {
	index := len(path) - 1
	for index >= 0 && path[index] != '/' && path[index] != '\\' {
		index--
	}
	if index <= 0 {
		return "."
	}
	return path[:index]
}

func (m *mappedRegion) flush() error {
	return nil
}

func (m *mappedRegion) close() error {
	var first error
	if m.data != nil {
		first = syscall.Munmap(m.data)
		m.data = nil
	}
	if m.file != nil {
		if err := m.file.Close(); first == nil {
			first = err
		}
		m.file = nil
	}
	return first
}
