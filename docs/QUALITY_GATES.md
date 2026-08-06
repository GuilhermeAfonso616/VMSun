# Gates de qualidade

Este documento define o minimo necessario para integrar uma mudanca no
Analitico. Os gates cobrem o backend Python, o gateway Go, o cliente operador e
os dois bancos suportados pelo projeto.

## Comando local

No Windows:

```powershell
.\scripts\check-project.ps1
```

Opcoes uteis:

```powershell
# Sem executar suites de teste; ainda compila Python e o cliente .NET
.\scripts\check-project.ps1 -SkipTests

# Maquina sem Go ou .NET
.\scripts\check-project.ps1 -SkipGo -SkipDotnet

# Gera coverage.xml e mostra a cobertura Python
.\scripts\check-project.ps1 -Coverage
```

No Linux:

```bash
bash scripts/check-project.sh
```

O comando completo executa:

1. compilacao dos fontes Python;
2. simulacao idempotente de upgrade de um schema SQLite legado;
3. suite Python;
4. testes do gateway Go;
5. build Release do cliente operador.

Diretorios temporarios ficam em `.tmp_quality_gate/`, ignorado pelo Git. Isso
evita depender do diretorio temporario global do host.

## Pipeline de CI

O workflow `.github/workflows/quality.yml` possui quatro jobs independentes:

- `Python tests and coverage`: suite Python, coverage e upgrade SQLite legado;
- `PostgreSQL schema compatibility`: cria o schema em PostgreSQL 16 e executa
  as migrations duas vezes;
- `Go gateway tests`: executa `go test ./...`;
- `Operator client build`: restaura e compila o app Avalonia em Windows.

O instalador nao faz parte do gate de cada commit porque exige a geracao de um
payload de release. Ele deve ser validado no checklist de homologacao.

## Protecao recomendada da branch principal

No provedor Git, marque os quatro jobs acima como obrigatorios e habilite:

- pull request antes do merge;
- branch atualizada antes do merge;
- bloqueio de merge com conversa pendente;
- impedimento de force-push na branch principal.

Essa configuracao e externa ao repositorio e deve ser aplicada por um
administrador do projeto.

## Politica de cobertura

O CI publica `coverage.xml` em toda execucao. Nesta primeira etapa a cobertura
e medida, mas ainda nao existe percentual minimo: impor um numero sem uma linha
de base confiavel esconderia lacunas atras de testes superficiais.

Depois de tres execucoes verdes consecutivas:

1. registrar a cobertura da branch principal;
2. impedir reducao de cobertura em novos pull requests;
3. exigir testes para autenticacao, migrations, eventos e notificacoes;
4. criar um projeto de testes .NET antes de evoluir o cliente operador.

A medicao inicial e as pendencias conhecidas ficam em
`docs/QUALITY_BASELINE.md`.

## Definicao de pronto

Uma mudanca esta pronta quando:

- os quatro jobs obrigatorios estao verdes;
- alteracoes de banco foram validadas em SQLite e PostgreSQL;
- existe caminho de rollback documentado;
- segredos e dados pessoais nao aparecem em logs ou artefatos;
- contratos HTTP alterados possuem teste;
- comportamento visivel ao operador esta documentado.
