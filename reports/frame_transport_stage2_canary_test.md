# Etapa 2A — teste canário real

Data: 2026-07-27 (America/Sao_Paulo)  
Branch: `checkpoint/ptz-3d-monitor-20260727`  
Commit inicial: `11932ac`  
Serviços: MediaMTX, Camera Gateway, runtime Python, pool central e aplicação web

## 1. Câmera selecionada

```text
ID: 37
Nome: NVR - Canal 6 sub
Codec: H.265
Resolução cadastrada/confirmada anteriormente: 704×576
FPS cadastrado: 15
Host sanitizado: 177.137.223.61
Porta RTSP: 554
Path MediaMTX: cam_37
```

A câmera 37 foi mantida porque continuava sem alarme ativo, sem sessão WebRTC
ou leitor MediaMTX de operador, sem worker antes do baseline, acessível e de
baixo impacto. A câmera 36 foi rejeitada porque tinha sessão de operador ativa.
Nenhuma URL RTSP ou credencial foi registrada.

O Gateway entrega frames redimensionados para 960×540 no pipeline analítico;
isso não altera a identificação cadastral acima.

## 2. Resultado funcional

O fluxo abaixo foi validado:

```text
NVR → MediaMTX/cam_37 → Gateway → mmap JPEG → worker → pool → inferência
```

Evidências:

- inode e tamanho idênticos nos dois containers;
- arquivo de 8.389.248 bytes com permissão `0660`;
- `shared_buffer_ready=true`;
- 127 escritas e 123 sobrescritas na amostra;
- 114 leituras do worker;
- zero erro de escrita;
- zero payload acima da capacidade;
- zero frame corrompido;
- zero fallback durante o período nominal;
- zero chamada ao endpoint HTTP por frame durante o período nominal;
- inferência ativa e fila central sem crescimento.

## 3. Comparação A/B curta

Foram coletadas dez amostras, a cada dois segundos, da mesma câmera. Outros
workers permaneceram ativos, portanto CPU de container é indicativa e não deve
ser usada isoladamente para declarar ganho definitivo.

| Métrica | HTTP | mmap | Variação |
|---|---:|---:|---:|
| CPU Camera Gateway | 148,02% | 146,79% | -0,83% |
| CPU container runtime | 116,06% | 67,69% | -41,68% |
| CPU worker 37 | 9,70% | 14,90% | +53,61% |
| RSS worker 37 | 644,10 MiB | 640,67 MiB | -0,53% |
| FPS processado | 1,24 | 1,21 | -2,42% |
| Inferência | 28,52 ms | 28,48 ms | -0,14% |
| Fila central | 0 | 0 | estável |
| Cópia/validação do mmap | n/d | 0,53 ms | n/d |
| Idade do frame no mmap | n/d | 1,92 ms | n/d |

O `read_ms` legado (10,25 ms) e o `shared_buffer_wait_ms` (499,69 ms) não são
comparáveis: o primeiro é uma resposta do ring HTTP e o segundo inclui a espera
pela próxima publicação do Gateway, configurada em aproximadamente 2 FPS. O
custo efetivo de leitura do mmap foi 0,53 ms.

Não foi observada regressão relevante de FPS ou inferência. A redução de CPU do
container precisa ser confirmada por soak controlado; o aumento do percentual
do processo individual também mostra que a janela curta tem ruído.

## 4. Reinícios e geração

### Worker

O worker foi reiniciado pelo fluxo `restart_existing`, sem parar a origem:

- geração do Gateway permaneceu igual;
- o Gateway escreveu mais 28 frames durante a janela;
- inferência retornou em 14,06 s;
- não houve corrupção.

### Gateway

No restart do Gateway:

- saúde HTTP do Gateway: 12,44 s;
- novo buffer/geração pronto: 17,13 s;
- primeira inferência após o novo buffer: 30,16 s desde o início;
- intervalo buffer pronto → inferência: 13,03 s;
- nova geração detectada;
- fallback HTTP: zero;
- frames corrompidos: zero.

O resultado melhora o canário anterior de quase um minuto, mas 30,16 s ainda é
alto para monitoramento. A maior parte restante ocorre na recriação da fonte,
inicialização/reconexão e agendamento da primeira inferência, não na leitura do
mmap. Recomenda-se uma tarefa específica de recuperação com metas por fase.

## 5. Fallback e modo estrito

Em `shared_memory_prefer`, o arquivo foi temporariamente renomeado de forma
controlada:

- fallback HTTP explícito ativado em cerca de 2 s;
- contador de fallback: 1;
- log `frame_transport_fallback` emitido;
- 342 requisições HTTP registradas durante a janela técnica de 20 s;
- retorno automático ao mmap em 0,72 s após restauração;
- zero corrupção.

Em `shared_memory_strict`:

- `/cameras/37/frames` respondeu HTTP 503;
- não houve fallback;
- o estado operacional ficou degradado após a idade de frame ultrapassar 15 s;
- a restauração do buffer retomou o fluxo sem reiniciar todo o runtime.

## 5.1 Canário da Etapa 2B (worker → pool)

Executado na mesma câmera 37, com `INFERENCE_TRANSPORT_MODE=binary_prefer` e
`INFERENCE_TRANSPORT_CAMERA_IDS=37`, mantendo a 2A em `shared_memory_prefer`.

Resultado funcional:

- 13 jobs binários submetidos na janela;
- 2,53 MB transportados sem Base64;
- zero erro de transporte;
- zero fallback;
- `inference_transport_mode` reportado como `binary_local`.

Latência ponta a ponta na janela medida:

| Métrica | HTTP | Socket binário | Variação |
|---|---|---|---|
| Latência de transporte até inferência | 114,34 ms | 66,19 ms | −42,1% |

Fallback em `binary_prefer`: o socket foi renomeado de forma controlada, o
fallback HTTP foi explícito (log + contador + `http_fallback` no estado) e o
transporte binário retomou automaticamente em 2,11 s após a restauração.

Modo `binary_strict`: com o socket indisponível, o fallback HTTP foi bloqueado e
o erro foi explícito, sem mascarar a câmera como offline.

Correções aplicadas durante este canário:

- `app/bootstrap.py` passou a tolerar falha ao abrir o servidor de socket nos
  modos não estritos, registrando degradação em vez de impedir a subida; em
  `binary_strict` a exceção propaga, como esperado;
- o cliente passou a validar `camera_id`, `job_id` e `generation_id` na resposta,
  rejeitando resultado cruzado ou de geração antiga;
- o adaptador de fallback passou a aceitar submissores HTTP que retornam duas ou
  três posições, preservando o `runtime` retornado.

Ressalva: a janela de 13 jobs comprova funcionamento e ausência de erro, mas é
curta demais para tratar os −42,1% como medida estável. O número deve ser
reconfirmado em janela longa antes de qualquer rollout.

## 6. Isolamento e capacidade

Os testes automatizados validaram:

- câmera divergente rejeitada;
- arquivo de uma câmera não entregue a outra;
- versão/magic incompatíveis rejeitados;
- slot parcialmente escrito e CRC inválido rejeitados;
- frame maior que o slot rejeitado sem truncamento;
- rotação, sobrescrita latest-only e mudança de geração.

O teste real de quatro câmeras não foi executado para não interferir nas sessões
ativas das outras candidatas.

## 7. Testes automatizados

- 24 testes Python direcionados: aprovados;
- `python -m compileall -q app tests`: aprovado;
- `go test ./...`: aprovado;
- `go vet ./...`: aprovado;
- Compose base + GPU: aprovado;
- suíte completa: 760 aprovados, 1 falha preexistente e fora do escopo;
- colisão dos testes Hikvision: não reproduzida;
- race Go: não executado por CGO indisponível.

## 8. Segurança

- sem credenciais em nomes, metadados ou relatórios;
- URL RTSP não impressa;
- mmap não exposto por rede;
- volume somente leitura no runtime;
- container web sem acesso ao volume;
- nenhum payload logado.

## 9. Rollback executado

Ao final, a câmera 37 foi parada e ambos os serviços voltaram para:

```env
FRAME_TRANSPORT_MODE=http
FRAME_TRANSPORT_CAMERA_IDS=
INFERENCE_TRANSPORT_MODE=http
```

O runtime retornou saudável e a câmera 37 ficou `stopped`, como antes do
canário. Não foi necessária limpeza manual de buffers.

## 10. Pendências

- soak mínimo de duas horas e posterior 24–72 horas;
- A/B exclusivo ou normalizado por carga, para 2A e 2B;
- canário da 2B em janela longa: a atual teve apenas 13 jobs;
- canário com quatro câmeras quando houver candidatas livres;
- teste real de payload acima da capacidade em janela controlada;
- reduzir recuperação do Gateway, hoje em 30,16 s até inferência.

## 11. Classificação

**Etapa 2A aprovada para canário.**

**Etapa 2B aprovada para canário.** Funcionou sem erro nem fallback, com
fallback e modo estrito verificados, mas a janela foi curta.

Nenhuma das duas está aprovada para rollout parcial ou global enquanto não
houver soak e A/B mais confiável. Ambos os modos permanecem em `http` por
padrão, e o rollback foi executado ao fim do teste.
