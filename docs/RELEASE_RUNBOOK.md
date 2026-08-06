# Runbook de release e rollback

O schema atual evolui por `Base.metadata.create_all()` seguido de migrations
aditivas e idempotentes. Nao existe downgrade estrutural automatico. Portanto,
o rollback seguro de uma versao que alterou o banco exige o backup feito antes
da atualizacao.

## 1. Pre-flight

Antes de publicar:

1. confirmar que o Quality Gate esta verde;
2. registrar commit, tag, versao do servidor e versao do cliente;
3. confirmar espaco livre para banco, snapshots e clips;
4. exportar o backup criptografado pela tela de Diagnostico;
5. fazer backup nativo do banco;
6. copiar `.env.docker` para o cofre operacional, sem versiona-lo;
7. registrar quais feature flags mudarao;
8. combinar uma janela e um responsavel pelo go/no-go.

Exemplo PostgreSQL em Docker:

```bash
docker compose exec -T postgres pg_dump -U analitico -d analitico -Fc > analitico_pre_release.dump
```

Em SQLite, pare os processos web/runtime antes de copiar o banco e inclua os
arquivos `-wal` e `-shm` caso ainda existam. Nao copie um SQLite ativo como se
fosse um arquivo estatico.

## 2. Atualizacao

```bash
git fetch --tags
git checkout <tag-aprovada>
./scripts/compose-auto.sh up -d --build
```

Nao altere varias feature flags ao mesmo tempo que publica uma nova imagem. A
imagem deve subir primeiro com o comportamento anterior; flags novas entram
depois da verificacao de saude.

## 3. Smoke test

Validar, nesta ordem:

1. `/health/live` e `/health/ready`;
2. conexao com o PostgreSQL;
3. gateway de cameras e MediaMTX;
4. login e perfis de acesso;
5. lista de cameras e monitor ao vivo;
6. start/stop de uma camera de homologacao;
7. persistencia de um evento com snapshot/clip;
8. reconhecimento e fechamento do evento;
9. backup de diagnostico;
10. cliente operador com a mesma versao de contrato do servidor.

Registre horario, operador, versoes e resultado. A release so e aceita depois
de ao menos 30 minutos sem erro novo de severidade alta.

## 4. Criterios de rollback

Inicie rollback quando ocorrer qualquer um destes casos:

- banco ou migrations nao inicializam;
- login indisponivel;
- cameras anteriormente saudaveis deixam de entregar frames em massa;
- eventos deixam de persistir;
- perda ou exposicao de dados;
- consumo de recursos excede o limite acordado sem estabilizar.

## 5. Rollback de aplicacao

Se a release nao alterou dados nem schema:

```bash
git checkout <tag-anterior>
./scripts/compose-auto.sh up -d --build
```

Se houve migration ou escrita incompatível:

1. parar web e runtime;
2. preservar banco e logs da versao que falhou para diagnostico;
3. restaurar o dump/backup pre-release;
4. voltar para a tag anterior;
5. subir a stack;
6. repetir o smoke test completo.

A restauracao e destrutiva para dados criados depois do backup. Ela deve ser
executada somente pelo responsavel da janela, com o alvo conferido e a perda de
dados registrada.

## 6. Fechamento

- anexar logs e resultado da homologacao ao registro da release;
- documentar incidentes e flags efetivamente ativadas;
- manter o backup pre-release durante o periodo de retencao acordado;
- atualizar o changelog e comunicar a versao do cliente operador.
