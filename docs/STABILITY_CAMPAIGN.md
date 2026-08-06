# Campanha de estabilidade e capacidade

O script `scripts/run_stability_campaign.py` coleta evidencias do caminho completo
camera -> gateway -> worker -> inferencia -> host e executa baseline, rampa e soak.

## O que e coletado

- Health da API web, runtime e Camera Gateway.
- Snapshot do supervisor: estado desejado e real, ownership, workers, pools e orfaos.
- Canario real de inferencia, com sucesso e latencia.
- Estado de todas as cameras e idade de frame, metrica e inferencia.
- Metricas completas dos workers a cada 60 segundos e conjunto critico a cada amostra.
- CPU, RAM e disco do host.
- GPU, VRAM, temperatura, potencia e driver via `nvidia-smi`.
- `docker stats` e `docker inspect`, incluindo reinicios, exit code, health e OOM.
- Novas linhas de todos os arquivos `*.log*` em `logs/` e `data/logs/`.
- Novas amostras dos historicos operacional e de recursos nativos.
- Commit, branch, worktree, versoes e parametros operacionais do `.env.docker`.

Senhas, tokens, chaves e credenciais embutidas em URLs sao mascarados antes da
persistencia. O script nao grava o valor do token do supervisor.

## Modo seguro: observacao

O comando abaixo nao inicia, para ou reinicia cameras:

```powershell
py -B scripts\run_stability_campaign.py observe --duration-minutes 15
```

Baseline de 72 horas:

```powershell
py -B scripts\run_stability_campaign.py observe --duration-hours 72 --stage-name baseline_72h
```

### Preset de sete dias

Para observar as cameras disponiveis por 168 horas, sem alterar o estado delas:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_stability_7d.ps1
```

O wrapper faz um preflight dos tres servicos, cria uma pasta
`stability_7d_TIMESTAMP` e chama o coletor com amostras a cada 15 segundos,
canario e detalhes a cada 60 segundos. A lista de cameras e descoberta no
preflight pelo snapshot do supervisor; nao existem IDs fixos no preset. Entram
as cameras marcadas como desejadas/ativas no runtime, inclusive as que estejam
temporariamente em aquecimento, degradadas ou reconectando. Se nenhuma camera
valida for encontrada, o gate `no_expected_cameras` reprova a execucao em vez de
fabricar um resultado. `-SkipPreflight` existe apenas para uma coleta deliberada
de indisponibilidade quando os servicos ja comecam fora do ar.

Para acompanhar apenas cameras especificas:

```powershell
py -B scripts\run_stability_campaign.py observe --duration-hours 72 --camera-ids "29,32,33,34,35,36,37"
```

O exit code e `0` quando todos os gates passam, `2` quando algum gate reprova e
`130` quando a execucao e interrompida pelo operador.

## Campanha completa

O modo `campaign` requer duas confirmacoes tecnicas:

1. `--camera-ids` com candidatas em ordem de ativacao.
2. `--allow-camera-control` para autorizar start/stop.

Exemplo para um ambiente que comeca com sete cameras ativas:

```powershell
py -B scripts\run_stability_campaign.py campaign `
  --camera-ids "38,39,40,41,42,43,44,45,46" `
  --ramp "8,10,12,14,16" `
  --baseline-hours 72 `
  --warmup-minutes 30 `
  --stage-minutes 120 `
  --soak-hours 168 `
  --allow-camera-control
```

Os numeros de `--ramp` sao o total desejado de cameras ativas, nao a quantidade
adicional. O script preserva as cameras que ja estavam ativas, ativa apenas as
candidatas necessarias e, por padrao, para somente as cameras que ele proprio
iniciou ao terminar ou abortar.

Antes da primeira alteracao, o preflight exige runtime, supervisor, gateway e
canario saudaveis. A campanha aborta no primeiro estagio reprovado por padrao.

Para manter as candidatas ativas depois do teste, use conscientemente:

```powershell
--no-restore-original-state
```

## Gates padrao

| Gate | Padrao |
|---|---:|
| Cobertura do coletor | >= 99% |
| Sucesso dos endpoints criticos | >= 99% |
| Disponibilidade das cameras esperadas | >= 99% |
| Sucesso do canario | >= 99% |
| Latencia p95 do canario | <= 2.000 ms |
| CPU p95 do host | <= 80% |
| RAM p95 do host | <= 80% |
| VRAM p95 | <= 80% |
| Descartes/falhas por inferencias submetidas | <= 0,1% |
| OOM, reinicio de container ou incidente aberto | zero |

Todos podem ser ajustados pela CLI. Consulte:

```powershell
py -B scripts\run_stability_campaign.py observe --help
py -B scripts\run_stability_campaign.py campaign --help
```

## Arquivos produzidos

Cada execucao cria `reports/stability_campaign/campaign_TIMESTAMP/` contendo:

- `manifest.json`: ambiente, versoes e parametros sanitizados.
- `telemetry.jsonl`: amostras completas da campanha.
- `logs.jsonl`: linhas novas dos logs de arquivo e containers.
- `native_history.jsonl`: copia incremental dos historicos nativos.
- `stage_NOME.json`: resultado estruturado por etapa.
- `stage_NOME.md`: relatorio legivel por etapa.
- `stage_NOME_cameras.csv`: disponibilidade e incidentes por camera.
- `campaign_summary.json` e `.md`: conclusao e limite provisorio com 25% de reserva.

Os arquivos sao atualizados com flush a cada amostra. Se o processo for encerrado
abruptamente, a telemetria ja coletada permanece utilizavel. `Ctrl+C` tambem gera
um relatorio parcial e restaura as cameras iniciadas pela campanha.

## Token opcional do supervisor

Se `SUPERVISOR_API_TOKEN` estiver configurado no servidor, exponha-o apenas na
sessao que executara o coletor:

```powershell
$env:ANALITICO_SUPERVISOR_TOKEN = "valor-do-token"
py -B scripts\run_stability_campaign.py observe --duration-minutes 15
Remove-Item Env:\ANALITICO_SUPERVISOR_TOKEN
```

## Cuidados operacionais

- Execute a rampa em janela controlada e com espaco livre suficiente em disco.
- A coleta detalhada ocorre a cada 60 segundos para limitar volume; ajuste com
  `--detail-interval-seconds` se necessario.
- O teste de reboot do host precisa de um coletor executado em outra maquina ou
  de uma tarefa agendada que reinicie o comando, pois um processo local nao pode
  observar o periodo em que o proprio host esta desligado.
- A capacidade homologada deve considerar codec, resolucao, FPS, perfil de cena
  e IAs habilitadas. O numero final nao deve ser generalizado para outro perfil.
