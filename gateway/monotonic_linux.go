//go:build linux

package main

import (
	"syscall"
	"unsafe"
)

func monotonicNowNS() uint64 {
	var value syscall.Timespec
	_, _, errno := syscall.Syscall(syscall.SYS_CLOCK_GETTIME, uintptr(1), uintptr(unsafe.Pointer(&value)), 0)
	if errno != 0 {
		return 0
	}
	return uint64(value.Sec)*1_000_000_000 + uint64(value.Nsec)
}
