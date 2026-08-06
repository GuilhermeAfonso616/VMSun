package main

import (
	"net/http"
	"os"
	"strings"
	"time"
)

func main() {
	addr := strings.TrimSpace(os.Getenv("GATEWAY_ADDR"))
	if addr == "" {
		addr = ":8090"
	}

	gateway := newGateway()

	mux := http.NewServeMux()
	gateway.registerRoutes(mux)

	server := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	serverLog.Info("listening", "addr", addr)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		serverLog.Error("listen_failed", "addr", addr, "error", err)
		os.Exit(1)
	}
}
