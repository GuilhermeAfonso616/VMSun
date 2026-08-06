# Segurança da instalação

## Primeiro acesso

Uma instalação nova não possui usuário ou senha padrão. Abra `/login`; o
servidor encaminhará para `/setup`, onde o primeiro administrador é criado.
Depois de concluído, o setup não reabre mesmo se o banco ficar sem usuários.

Na atualização de uma instalação antiga:

- usuários existentes são preservados;
- `admin/admin`, quando ainda existir, é obrigado a trocar a senha;
- `dev/dev123`, quando ainda existir, é desativado;
- ao menos um administrador ativo deve permanecer cadastrado.

## Senhas e sessões

Por padrão, senhas novas exigem 12 caracteres, maiúscula, minúscula e número.
O mínimo pode ser aumentado por `PASSWORD_MIN_LENGTH`.

O login continua retornando Bearer token para o cliente desktop, mas o browser
recebe também um cookie emitido pelo servidor, `HttpOnly` e `SameSite=Lax`.
Em HTTPS o atributo `Secure` é automático; em proxy TLS, defina
`SESSION_COOKIE_SECURE=true` se o esquema original não for encaminhado. As
sessões duram sete dias por padrão (`SESSION_TTL_SECONDS=604800`), ficam no
banco e são revogadas no logout, na troca de senha e ao desativar o usuário.

## Credenciais de câmera e backup

Senhas de câmera são cifradas antes de chegar ao banco. A chave é gerada em
`runtime_state/credential_encryption_key` (no Docker,
`/data/runtime_state/credential_encryption_key`). Não apague nem substitua esse
arquivo: sem ele, as credenciais existentes não podem ser recuperadas.

O backup administrativo criptografado inclui essa chave e a restauração a
repõe junto do banco. Reinicie o servidor depois da restauração para que a
chave restaurada seja carregada.
