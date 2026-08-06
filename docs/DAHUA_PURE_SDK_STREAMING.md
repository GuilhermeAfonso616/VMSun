# Opção 2 — vídeo contínuo pelo Dahua NetSDK

## Objetivo

Esta opção elimina o RTSP do caminho de reprodução e mantém uma conexão persistente com o equipamento pelo Dahua NetSDK. O SDK continua responsável por login, descoberta e PTZ, mas também entrega o fluxo de vídeo a um serviço nativo dedicado.

Ela não está implementada no laboratório atual. O fluxo adotado agora é o híbrido: controle pelo SDK e vídeo RTSP convertido para WebRTC pelo MediaMTX.

## Arquitetura proposta

```text
NVR/câmera Dahua
       │ NetSDK (sessão persistente)
       ▼
Worker nativo isolado
  ├─ login/reconexão
  ├─ CLIENT_RealPlayEx
  ├─ callback de dados em tempo real
  ├─ PlaySDK/demux e decodificação
  └─ buffers limitados e métricas
       │ frames ou stream compatível
       ▼
Encoder H.264 de baixa latência
       │ WebRTC
       ▼
Navegador
```

O worker deve rodar fora do processo web. Callbacks nativos e estruturas ABI incorretas podem encerrar o processo; o isolamento impede que uma falha do SDK derrube a aplicação administrativa.

## Fluxo técnico

1. Abrir uma sessão persistente com `CLIENT_LoginWithHighLevelSecurity`.
2. Iniciar o canal com `CLIENT_RealPlayEx` sem janela nativa.
3. Registrar o callback de dados em tempo real, usando a função equivalente disponível na versão instalada do NetSDK, como `CLIENT_SetRealDataCallBackEx2`.
4. Entregar o stream privado ao PlaySDK para demultiplexação e decodificação. Uma alternativa é repassar o stream bruto ao FFmpeg quando o formato retornado pelo equipamento estiver devidamente identificado e suportado.
5. Codificar para H.264 com baixa latência, preferencialmente por hardware quando houver várias sessões simultâneas.
6. Publicar o resultado em WebRTC e manter PTZ na mesma sessão autenticada.

Os nomes e assinaturas definitivos precisam ser conferidos nos headers e exemplos que acompanham exatamente a versão do SDK homologada. Não se deve copiar estruturas `ctypes` de outra versão sem validar tamanhos, alinhamento e convenção de chamada.

## Serviço persistente

O processo web deve conversar com o worker por IPC local autenticado, por exemplo socket Unix dentro do container ou gRPC restrito à rede interna. O protocolo mínimo precisa oferecer:

- criar e encerrar sessão;
- iniciar e parar canal;
- executar PTZ;
- consultar estado e métricas;
- renovar o tempo de vida da sessão;
- receber somente um identificador opaco, nunca a senha de volta.

Cada sessão precisa de watchdog, reconexão com espera progressiva, limite de memória, fila curta e descarte de frames atrasados. Para vídeo ao vivo, preservar frames antigos aumenta o atraso; quando o consumidor fica lento, a política correta é avançar até o frame atual.

## Segurança

- Credenciais entram pelo `stdin` ou IPC protegido, nunca por argumentos de processo.
- Senhas e URLs autenticadas não aparecem em logs, métricas ou respostas HTTP.
- O worker roda com usuário sem privilégios, filesystem somente leitura e limites de CPU/memória.
- Sessões expiram automaticamente e o logout do SDK é executado no encerramento normal e no watchdog.
- Apenas administradores autorizados podem criar sessões e enviar PTZ.
- Bibliotecas do fabricante ficam fixadas por versão e SHA-256.

## Compatibilidade e desempenho

O navegador normalmente exige H.264 para maior compatibilidade WebRTC. Se o canal fornecer H.265, será necessário transcodificar. Isso torna obrigatório medir:

- CPU e GPU por canal;
- memória e crescimento das filas;
- tempo até o primeiro frame;
- latência ponta a ponta;
- perda de frames;
- tempo e taxa de sucesso de reconexão;
- estabilidade por pelo menos 24 a 72 horas.

Antes de escalar, devem ser homologados câmera direta e NVR, canais analógicos e digitais, main stream e substream, H.264/H.265, quedas de rede, senha incorreta e reinicialização do equipamento.

## Etapas de implementação

1. Criar um protótipo isolado que abre um único canal e grava dez segundos do stream recebido pelo callback.
2. Validar o formato e integrar o PlaySDK ou demuxer adequado.
3. Publicar um único canal em WebRTC, medindo latência e consumo.
4. Adicionar sessão persistente, PTZ, timeout, reconexão e encerramento seguro.
5. Implementar IPC autenticado entre aplicação e worker.
6. Adicionar limites, métricas, testes de falha e testes prolongados.
7. Homologar modelos e firmwares suportados antes de liberar comercialmente.

## Critérios de aceite

- Primeiro frame em até 5 segundos em condições normais.
- Latência estável e sem crescimento contínuo de memória.
- Reconexão automática após interrupção de rede ou reinício do NVR.
- Nenhuma credencial em logs, comandos, respostas ou arquivos temporários.
- Uma falha nativa não derruba o servidor web.
- PTZ e vídeo usam a mesma sessão ou um pool controlado de sessões.
- Teste prolongado aprovado na capacidade comercial definida para o servidor.

## Motivo para manter como segunda etapa

A solução oferece integração mais profunda, mas adiciona risco nativo, decodificação, possível transcodificação e gerenciamento permanente de conexões. O fluxo híbrido entrega vídeo contínuo com menos código proprietário e permite validar canais, PTZ, codecs e conectividade antes de assumir essa complexidade.
