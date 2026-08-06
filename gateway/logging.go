package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"
	"sync"
	"time"

	// Embute o banco de fusos no binario: a imagem final e alpine sem tzdata, e sem
	// isto o gateway cairia para UTC enquanto o lado Python loga em horario de Brasilia.
	_ "time/tzdata"
)

// Mesmo layout do ContextFormatter do Python (app/core/logging.py), para que os dois
// lados do sistema possam ser lidos e filtrados com os mesmos comandos:
//
//	2026-07-29 09:41:43 | INFO     | gateway.orch | cam=37 | action=starting | priority=5
//
// A mensagem do slog e a acao (os call sites ja usavam tokens como "loop_start"),
// entao ela sai em action= em vez de texto solto no fim da linha.
const logTimeLayout = "2006-01-02 15:04:05"

var brazilLocation = func() *time.Location {
	loc, err := time.LoadLocation("America/Sao_Paulo")
	if err != nil {
		return time.UTC
	}
	return loc
}()

type contextHandler struct {
	mu    *sync.Mutex
	out   io.Writer
	level slog.Leveler
	name  string
	attrs []slog.Attr
}

func (h *contextHandler) Enabled(_ context.Context, level slog.Level) bool {
	return level >= h.level.Level()
}

func (h *contextHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	if len(attrs) == 0 {
		return h
	}
	combinados := make([]slog.Attr, 0, len(h.attrs)+len(attrs))
	combinados = append(combinados, h.attrs...)
	combinados = append(combinados, attrs...)
	return &contextHandler{mu: h.mu, out: h.out, level: h.level, name: h.name, attrs: combinados}
}

// WithGroup nao agrupa: o formato e uma linha plana de pares chave=valor.
func (h *contextHandler) WithGroup(name string) slog.Handler {
	return h
}

func formatValue(value slog.Value) string {
	texto := value.String()
	if texto == "" {
		return "-"
	}
	// Espaco e barra vertical quebrariam o parsing por campo da linha.
	texto = strings.ReplaceAll(texto, "|", "/")
	return strings.Join(strings.Fields(texto), "_")
}

func (h *contextHandler) Handle(_ context.Context, record slog.Record) error {
	campos := make([]slog.Attr, 0, len(h.attrs)+record.NumAttrs())
	campos = append(campos, h.attrs...)
	record.Attrs(func(attr slog.Attr) bool {
		campos = append(campos, attr)
		return true
	})

	var builder strings.Builder
	builder.WriteString(record.Time.In(brazilLocation).Format(logTimeLayout))
	builder.WriteString(fmt.Sprintf(" | %-8s | %s | ", record.Level.String(), h.name))

	// cam vem primeiro, como no Python, para alinhar a leitura das duas fontes.
	for _, attr := range campos {
		if attr.Key == "cam" {
			builder.WriteString("cam=" + formatValue(attr.Value) + " | ")
			break
		}
	}

	builder.WriteString("action=" + strings.ReplaceAll(record.Message, " ", "_"))

	for _, attr := range campos {
		if attr.Key == "cam" {
			continue
		}
		builder.WriteString(" | " + attr.Key + "=" + formatValue(attr.Value))
	}
	builder.WriteByte('\n')

	h.mu.Lock()
	defer h.mu.Unlock()
	_, err := io.WriteString(h.out, builder.String())
	return err
}

var logWriteMutex sync.Mutex

func newComponentLogger(name string) *slog.Logger {
	level := slog.LevelInfo
	if parsed := strings.ToUpper(strings.TrimSpace(os.Getenv("GATEWAY_LOG_LEVEL"))); parsed != "" {
		switch parsed {
		case "DEBUG":
			level = slog.LevelDebug
		case "WARNING", "WARN":
			level = slog.LevelWarn
		case "ERROR":
			level = slog.LevelError
		}
	}
	return slog.New(&contextHandler{
		mu:    &logWriteMutex,
		out:   os.Stdout,
		level: level,
		name:  name,
	})
}

// Um logger por componente, espelhando os prefixos [ORCH], [CAPTURE], ... anteriores.
var (
	orchLog      = newComponentLogger("gateway.orch")
	captureLog   = newComponentLogger("gateway.capture")
	healthLog    = newComponentLogger("gateway.health")
	recoveryLog  = newComponentLogger("gateway.recovery")
	fallbackLog  = newComponentLogger("gateway.fallback")
	transportLog = newComponentLogger("gateway.frame_transport")
	serverLog    = newComponentLogger("gateway.server")
)
