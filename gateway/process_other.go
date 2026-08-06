//go:build !linux && !darwin && !freebsd && !openbsd && !netbsd && !dragonfly && !solaris

package main

import "os/exec"

func configureCommandProcess(cmd *exec.Cmd) {
}

func terminateCommand(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	_ = cmd.Process.Kill()
}
