# Notificações de alarmes

## Escopo

A central em `/notificacoes` permite que administradores configurem um ou
mais webhooks. Supervisores podem consultar o histórico e reenviar entregas.
Cada canal define severidade mínima, tipos de evento opcionais, timeout e
quantidade máxima de tentativas.

Somente eventos com alarme elegível e ativo, ciclo `open` e status persistido
entram na fila. A chave `event:{event_id}:channel:{channel_id}:type:{type}` impede duplicata
para o mesmo destino.

## Contrato HTTP

O dispatcher envia `POST` com JSON e os cabeçalhos:

- `Content-Type: application/json`;
- `X-Analitico-Delivery`: chave idempotente, que o receptor deve deduplicar;
- `X-Analitico-Signature`: HMAC-SHA256 hexadecimal do corpo, presente quando o
  canal possui segredo de assinatura.

O receptor deve responder com qualquer status `2xx`. Respostas e erros são
truncados antes de serem armazenados.

## Retentativa e recuperação

Falhas usam backoff exponencial a partir de
`NOTIFICATION_RETRY_BASE_SECONDS` (30 segundos por padrão). Ao alcançar o
limite do canal, a entrega passa para `dead` e pode ser reenviada na interface.
A fila fica no banco; itens que estavam em `processing` durante uma queda são
recuperados automaticamente depois do timeout de processamento.

Entregas concluídas ou mortas são mantidas por 90 dias. Ajuste com
`NOTIFICATION_DELIVERY_RETENTION_DAYS`; use `0` apenas quando a retenção
ilimitada for realmente necessária.

O dispatcher pode ser desligado com `NOTIFICATION_DISPATCH_ENABLED=false`.
Essa flag impede novos enfileiramentos automáticos, mas preserva o histórico.

## Segurança

URL e segredo de assinatura usam a mesma criptografia em repouso das
credenciais das câmeras. A API e a interface nunca devolvem query strings da
URL (caminho e query string são ocultados) nem o segredo. Use HTTPS sempre que
o receptor suportar e faça a validação
da assinatura sobre os bytes exatos recebidos antes de processar o payload.
