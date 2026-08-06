package main

import (
	"bytes"
	"context"
	"log/slog"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

func loggerParaTeste(buffer *bytes.Buffer, nome string) *slog.Logger {
	return slog.New(&contextHandler{
		mu:    &sync.Mutex{},
		out:   buffer,
		level: slog.LevelInfo,
		name:  nome,
	})
}

// O formato precisa casar com o ContextFormatter do Python para que os dois lados
// sejam filtraveis pelos mesmos comandos.
func TestFormatoAlinhadoComPython(t *testing.T) {
	var buffer bytes.Buffer
	logger := loggerParaTeste(&buffer, "gateway.orch")

	logger.Info("starting", "cam", 37, "priority", 5, "reason", "manual")

	linha := strings.TrimRight(buffer.String(), "\n")
	padrao := regexp.MustCompile(
		`^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| INFO {5}\| gateway\.orch \| cam=37 \| action=starting \| priority=5 \| reason=manual$`,
	)
	if !padrao.MatchString(linha) {
		t.Fatalf("linha fora do formato esperado:\n%s", linha)
	}
}

func TestCamSempreVemPrimeiro(t *testing.T) {
	var buffer bytes.Buffer
	logger := loggerParaTeste(&buffer, "gateway.capture")

	logger.Warn("degraded", "last_frame_age_ms", 900, "cam", 12)

	linha := buffer.String()
	posCam := strings.Index(linha, "cam=12")
	posIdade := strings.Index(linha, "last_frame_age_ms=900")
	if posCam < 0 || posIdade < 0 || posCam > posIdade {
		t.Fatalf("cam deveria vir antes dos demais campos: %s", linha)
	}
	if !strings.Contains(linha, "| WARN ") {
		t.Fatalf("nivel WARN ausente: %s", linha)
	}
}

func TestValoresNaoQuebramOsCampos(t *testing.T) {
	var buffer bytes.Buffer
	logger := loggerParaTeste(&buffer, "gateway.capture")

	logger.Error("failed", "cam", 3, "error", "connection refused | retry")

	linha := strings.TrimRight(buffer.String(), "\n")
	// O separador do formato e " | ": um valor com espaco ou barra criaria campos falsos.
	// Esperado: ts | LEVEL | logger | cam | action | error  ->  5 separadores.
	if strings.Count(linha, " | ") != 5 {
		t.Fatalf("valor com espaco/barra vazou para o separador: %s", linha)
	}
	if !strings.Contains(linha, "error=connection_refused_/_retry") {
		t.Fatalf("valor deveria ter sido normalizado: %s", linha)
	}
}

func TestNivelAbaixoDoMinimoNaoEmite(t *testing.T) {
	var buffer bytes.Buffer
	logger := loggerParaTeste(&buffer, "gateway.orch")

	logger.Debug("nao_deve_aparecer", "cam", 1)

	if buffer.Len() != 0 {
		t.Fatalf("DEBUG nao deveria ser emitido com nivel INFO: %s", buffer.String())
	}
}

func TestHorarioEmBrasilia(t *testing.T) {
	var buffer bytes.Buffer
	handler := &contextHandler{mu: &sync.Mutex{}, out: &buffer, level: slog.LevelInfo, name: "gateway.server"}

	// 12:00 UTC = 09:00 em Sao Paulo (UTC-3).
	registro := slog.NewRecord(time.Date(2026, 7, 29, 12, 0, 0, 0, time.UTC), slog.LevelInfo, "listening", 0)
	if err := handler.Handle(context.Background(), registro); err != nil {
		t.Fatalf("Handle retornou erro: %v", err)
	}

	if !strings.HasPrefix(buffer.String(), "2026-07-29 09:00:00 ") {
		t.Fatalf("horario nao foi convertido para Brasilia: %s", buffer.String())
	}
}

func TestEscritaConcorrenteNaoMisturaLinhas(t *testing.T) {
	var buffer bytes.Buffer
	logger := loggerParaTeste(&buffer, "gateway.capture")

	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			logger.Info("loop_start", "cam", id)
		}(i)
	}
	wg.Wait()

	linhas := strings.Split(strings.TrimRight(buffer.String(), "\n"), "\n")
	if len(linhas) != 50 {
		t.Fatalf("esperado 50 linhas, obtido %d", len(linhas))
	}
	for _, linha := range linhas {
		if !strings.Contains(linha, "action=loop_start") {
			t.Fatalf("linha corrompida por escrita concorrente: %q", linha)
		}
	}
}
