# Linha de base de qualidade

Medicao local executada em 21/07/2026 durante a Etapa 0.

## Resultado

| Componente | Resultado |
|---|---|
| Compilacao Python | aprovado |
| Suite Python | 579 testes aprovados |
| Cobertura Python | 62% de statements (`24.057` statements; `9.134` nao cobertos) |
| Upgrade SQLite legado | aprovado e idempotente |
| Schema SQLite novo | aprovado e idempotente |
| Gateway Go | `go test ./...` aprovado |
| Cliente operador | build Release aprovado, zero erros e zero avisos |
| PostgreSQL 16 | schema novo aprovado em container descartavel |

## Dividas visiveis

- A suite com cobertura emitiu 16 `ResourceWarning` relacionados a conexoes
  SQLite nao fechadas em alguns testes.
- Nao existe projeto de testes automatizados para o cliente .NET.
- O percentual de cobertura ainda nao bloqueia merge; primeiro deve ser
  confirmado por tres execucoes verdes na CI.
- A migration atual e aditiva e nao possui downgrade automatico. O rollback de
  banco depende do backup pre-release descrito no runbook.
- O workflow precisa ser executado no provedor Git antes de seus jobs serem
  marcados como checks obrigatorios da branch.

Durante a primeira execucao PostgreSQL, o gate encontrou defaults booleanos em
duas colunas inteiras. Os defaults de `users.login_attempts` e
`lockdown_deliveries.attempt_count` foram corrigidos para `0` e cobertos por
teste de regressao do DDL PostgreSQL.

## Proxima revisao

Atualize esta linha de base quando:

- a CI completar tres execucoes verdes;
- um projeto de testes .NET for criado;
- warnings de conexao forem eliminados;
- o mecanismo de migration/downgrade mudar.
