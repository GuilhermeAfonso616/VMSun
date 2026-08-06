# Etapa 3B — canário da pool central da IA2

Data: 2026-07-27. Ambiente: Docker Compose local (CPU), imagem do runtime
reconstruída para incluir a Etapa 3B.

---

## 1. Câmera selecionada

| Campo | Valor |
|---|---|
| camera_id | 37 |
| motivo da seleção | única entre as 7 câmeras do snapshot já usada como canário nas Etapas 1 e 2, sem operador ativo e sem alarme |
| estado inicial | `degraded` (sem worker ativo) |
| worker durante o teste | PID 103 (central) e PID 82 (local), processos filhos do runtime |

Nenhum ID foi fixado em código: a seleção veio do snapshot de
`/internal/health/cameras` e é controlada por `IA2_CENTRAL_CAMERA_IDS`.

Credenciais e URLs RTSP não foram registradas.

---

## 2. Baseline — tudo local

```env
IA2_EXECUTION_MODE=local
IA2_POOL_ENABLED=false
```

`/internal/health/cameras` → `ia2_pool: {"enabled": false, "ready": false, "state": "disabled"}`

Confirma que, sem habilitar, nada muda: a pool nem existe.

---

## 3. Pool ativa — `central_prefer`

```env
IA2_POOL_ENABLED=true
IA2_EXECUTION_MODE=central_prefer
IA2_CENTRAL_CAMERA_IDS=37
IA2_TRANSPORT_MODE=binary_prefer
```

Logs do runtime na subida:

```
action=ia2_pool_started   Pool IA2 iniciada generation=1 workers=1 queue_capacity=64
action=ia2_socket_started Servidor de socket da IA2 iniciado path=/run/sunorus/ia2.sock
action=ia2_pool_ready     Pool IA2 pronta generation=1
                          model=/models/revalidator/person_crop_revalidator_yolo11n_v5.pt
                          device=cpu load_seconds=1.235
```

Health da pool:

```json
{"enabled": true, "ready": true, "state": "ready", "generation": 1,
 "backend": "ultralytics", "model_loaded": true, "device": "cpu",
 "queue_size": 0, "queue_capacity": 64, "workers": 1,
 "model_load_seconds": 1.235, "last_error": null, "restarts_total": 0}
```

Métricas da câmera 37 após ~25 s de operação real:

```json
{"ia2_execution_mode": "central", "ia2_configured_mode": "central_prefer",
 "ia2_pool_ready": true, "ia2_fallback_active": false,
 "ia2_last_latency_ms": 162.507, "ia2_last_queue_wait_ms": 0.0,
 "ia2_fallback_total": 0, "ia2_identity_rejected_total": 0,
 "ia2_timeouts_total": 0, "ia2_last_error": null}
```

**Jobs reais da IA2 foram executados pela pool central**, sem fallback, sem
timeout e sem rejeição de identidade. Contagem de `ia2_pool_job_failed`,
`ia2_pool_queue_full` e `ia2_pool_timeout` no log: **zero**.

---

## 4. Modo estrito — `central_strict`

```env
IA2_EXECUTION_MODE=central_strict
IA2_TRANSPORT_MODE=binary_strict
```

```json
{"ia2_execution_mode": "central", "ia2_configured_mode": "central_strict",
 "ia2_pool_ready": true, "ia2_fallback_active": false,
 "ia2_last_latency_ms": 34.583, "ia2_fallback_total": 0,
 "ia2_timeouts_total": 0}
```

A inferência continuou funcionando com a pool obrigatória e sem nenhum fallback.

---

## 5. A/B medido

Medição por `ps -eo pid,rss` dentro do container, com a câmera 37 ativa nos dois
modos.

| Métrica | IA2 local | IA2 central (strict) | Variação |
|---|---:|---:|---:|
| RSS do worker da câmera | 740 MB | 740 MB | **0%** |
| RSS do runtime | 1.407 MB | 1.483 MB | +76 MB |
| `ia2_last_latency_ms` | 271,31 | 34,58 | ver ressalva |
| Fallbacks | — | 0 | — |
| Timeouts | — | 0 | — |
| Jobs stale / identidade rejeitada | — | 0 | — |
| Fila (queue_size / wait) | — | 0 / 0,0 ms | — |
| Instâncias IA2 carregadas | 0 no worker (lazy, não acionada) | 1 na pool | — |

### O que estes números realmente dizem

**Não houve economia de RAM demonstrada, e isso era esperado nesta janela.** O
worker manteve exatamente 740 MB nos dois modos porque, no baseline local, a IA2
**nunca chegou a ser carregada** — o carregamento é lazy e não houve evento de
pessoa suficiente para acioná-la. Os 740 MB são o custo base do worker (torch,
IA1, captura), não incluem IA2 em nenhum dos casos.

Ou seja: este canário comprova que a **pool funciona**, não que ela **economiza
memória**. A economia só aparece quando vários workers efetivamente executam
IA2 — cenário que exige carga real com pessoas em cena, ainda não medido.

O custo da centralização, esse sim, ficou medido: **+76 MB no runtime**, que é o
modelo IA2 carregado uma vez.

**A comparação de latência não é válida como ganho.** Os 271,31 ms do modo local
correspondem à primeira inferência do worker, que inclui o carregamento lazy do
modelo; os 34,58 ms da pool são com modelo já quente. É a diferença entre frio e
quente, não entre arquiteturas. O que se pode afirmar é que a pool **elimina o
custo de warm-up por worker**, porque o modelo é carregado uma vez, na subida do
runtime (1,235 s), e não a cada worker novo.

---

## 6. Testes não executados em ambiente real

- **Falha da pool com a câmera em operação** (parar a pool e observar fallback em
  `prefer` e degradação segura em `strict`): coberto por teste automatizado
  (`test_strict_nao_executa_modelo_local_quando_pool_cai`,
  `test_prefer_cai_para_local_quando_pool_cai`), **não** reproduzido no canário.
- **Quatro câmeras simultâneas**: não executado.
- **Soak de 2 h**: não executado.
- **Medição de VRAM**: a pool subiu em `device=cpu` neste ambiente; não há número
  de VRAM para comparar.
- **Tempo de startup do worker**: não isolado o suficiente para virar número.

### Interferência registrada

Durante o canário, o ambiente Docker foi recriado por **outra sessão de trabalho
em paralelo** — as variáveis do runtime voltaram sozinhas para
`IA2_POOL_ENABLED=false` / `IA2_EXECUTION_MODE=local` entre duas medições. Os
dados das seções 3 e 4 foram coletados antes e depois dessa interferência, em
execuções distintas. Optei por não continuar disputando o ambiente para não
atrapalhar o trabalho paralelo, e é por isso que o teste de falha e o de quatro
câmeras ficaram de fora.

---

## 7. Rollback executado

```env
IA2_EXECUTION_MODE=local
IA2_CENTRAL_CAMERA_IDS=
IA2_POOL_ENABLED=false
IA2_TRANSPORT_MODE=http
```

Confirmado no container após o teste, com o runtime saudável
(`{"status":"ready","ready":true,"reason":"detector_probe_ok"}`). Nenhuma
alteração de banco, evento ou modelo foi necessária.

---

## 8. Critérios de aprovação

| # | Critério | Resultado |
|---|---|---|
| 1 | Pool carrega e fica pronta | ✅ `ready`, 1,235 s |
| 2 | Jobs chegam à pool | ✅ latência real medida na câmera |
| 3 | Resultados equivalentes ao local | ✅ por teste automatizado (13 testes) |
| 4 | Strict não carrega IA2 local | ✅ por teste automatizado; em produção o worker não cresceu |
| 5 | Falha não vira REJECT | ✅ por teste automatizado |
| 6 | Timeout não vira REJECT | ✅ por teste automatizado |
| 7 | Fila é limitada | ✅ capacity 64, teste de fila cheia aprovado |
| 8 | Identidade validada | ✅ 0 rejeições no canário, testes de divergência aprovados |
| 9 | Stale descartado | ✅ teste de deadline expirado aprovado |
| 10 | IA1 sem regressão | ✅ runtime `ready`, `detector_probe_ok` |
| 11 | Worker vivo durante falha | ⚠️ só em teste automatizado |
| 12 | Recuperação automática | ⚠️ não medida em ambiente real |
| 13 | Testes passam | ✅ 830 aprovados |
| 14 | Sem credenciais/crops em log | ✅ auditado |

---

## 9. Classificação

**Etapa 3B: aprovada para canário.**

Não aprovada para rollout parcial: faltam o teste de falha em ambiente real,
quatro câmeras, soak de 2 h e — principalmente — uma medição de carga que
demonstre a economia de memória, que é a justificativa da centralização e ainda
não foi comprovada.
