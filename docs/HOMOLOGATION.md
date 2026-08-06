# Ambiente de homologacao

A homologacao deve ser isolada da producao, usar banco proprio e nunca enviar
notificacoes para destinatarios reais.

## Topologia minima

- stack Docker igual a producao;
- PostgreSQL exclusivo de homologacao;
- uma camera real nao critica, quando disponivel;
- cameras RTSP simuladas por clips revisados;
- usuario administrador, supervisor, operador e visualizador de teste;
- armazenamento e credenciais separados de producao.

## Cameras reproduziveis

O fluxo existente em `docs/clip_replay_testing.md` gera um manifesto e publica
clips em loop no MediaMTX. Mantenha no conjunto de homologacao:

- um verdadeiro positivo;
- um falso positivo;
- um clip sem pessoa;
- um stream que desconecta e retorna;
- um clip H.264 e, quando suportado, um H.265.

Os dados grandes continuam fora do Git. Versione somente o manifesto sanitizado
e a expectativa de cada caso quando os direitos sobre o material permitirem.

## Roteiro por release

1. restaurar um backup sanitizado ou criar banco vazio;
2. executar `scripts/verify_database_compatibility.py` no banco descartavel;
3. subir a tag candidata com flags conservadoras;
4. executar o smoke test do runbook;
5. publicar a sequencia RTSP de clips;
6. comparar alarmes observados com as expectativas do manifesto;
7. testar queda e retorno do gateway;
8. medir CPU, RAM, GPU, FPS e crescimento de disco;
9. executar backup e restauracao em uma segunda instancia descartavel;
10. gerar o instalador e testar instalacao, upgrade e desinstalacao no Windows.

## Evidencias da homologacao

O registro deve conter:

- tag/commit e data;
- versoes de Python, Go, .NET, PostgreSQL e drivers de GPU;
- quantidade e codecs das cameras;
- resultado do Quality Gate;
- eventos esperados versus observados;
- consumo maximo de recursos;
- resultado do backup/restore;
- decisao go/no-go e responsavel.
