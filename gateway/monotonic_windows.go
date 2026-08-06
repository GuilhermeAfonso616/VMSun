//go:build windows

package main

import "time"

var windowsMonotonicOrigin = time.Now()

func monotonicNowNS() uint64 {
	return uint64(time.Since(windowsMonotonicOrigin).Nanoseconds())
}
