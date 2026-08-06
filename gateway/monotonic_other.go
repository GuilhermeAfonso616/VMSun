//go:build !linux && !windows

package main

import "time"

var portableMonotonicOrigin = time.Now()

func monotonicNowNS() uint64 {
	return uint64(time.Since(portableMonotonicOrigin).Nanoseconds())
}
