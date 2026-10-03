# E-mail de alertas — HST Alerta

## Canal principal
O HST Alerta utiliza e-mail via Resend para notificações automáticas do Grupo Operacional.

## Política inicial
- Nível 1 — sem envio.
- Nível 2 — informativo opcional; inicialmente sem envio automático.
- Nível 3 — pré-alerta operacional.
- Nível 4 — alerta de gestão.
- Nível 5 — alerta máximo.
- Não repetir a mesma mensagem a cada coleta.
- Enviar novamente quando houver agravamento de nível.
- Permitir nova mensagem no mesmo nível quando o contexto predominante mudar de forma relevante.
- Após um nível notificado, enviar uma única atualização quando houver rebaixamento.

## Secrets necessários no GitHub
Cadastrar em Settings > Secrets and variables > Actions:

- `RESEND_API_KEY` — chave nova do Resend, preferencialmente com permissão Sending access.
- `HST_EMAIL_GRUPO_OPERACIONAL` — e-mail que receberá os alertas.
- `HST_EMAIL_ENABLED` — usar `true` somente depois do teste.
- `HST_EMAIL_FROM` — opcional. Se não informado, o teste usa `HST Alerta <onboarding@resend.dev>`.
- `HST_EMAIL_REPLY_TO` — opcional.

## Segurança
- Não gravar a chave do Resend em HTML, JavaScript, Python ou arquivos públicos.
- Não gravar o e-mail do destinatário em arquivos públicos.
- Se uma chave tiver sido compartilhada por chat, mensagem ou outro canal, revogá-la e criar outra.
- Para o HST Alerta, uma chave com acesso apenas a envio é suficiente.

## Funcionamento técnico
O workflow principal:
1. coleta as fontes;
2. calcula o Nível HST;
3. valida o site;
4. avalia se existe evento de notificação;
5. envia o e-mail quando o serviço estiver configurado e habilitado;
6. registra o estado em `data/email_notification_state.json`;
7. persiste histórico e publica o site.

## Conteúdo do e-mail
Cada alerta contém:
- nível HST;
- tipo de evento (novo alerta, agravamento, atualização ou melhora);
- contexto predominante;
- motivo;
- ações prioritárias;
- horário de atualização;
- link para o HST Alerta.

## Ativação
Enquanto `HST_EMAIL_ENABLED` não estiver como `true`, o sistema permanece preparado, porém sem envio automático.


## Alternativa gratuita sem domínio verificado — Gmail SMTP
Quando o Resend estiver limitado ao e-mail da própria conta de teste, o HST Alerta pode usar Gmail SMTP.

Secrets:
- `HST_SMTP_USER` — conta Gmail remetente.
- `HST_SMTP_APP_PASSWORD` — senha de app de 16 caracteres criada na Conta Google.
- `HST_SMTP_HOST` — opcional; padrão `smtp.gmail.com`.
- `HST_SMTP_PORT` — opcional; padrão `465`.
- `HST_EMAIL_FROM` — opcional; pode ser omitido para usar o Gmail como remetente.

A senha normal da conta Google não deve ser usada. A senha de app exige verificação em duas etapas na Conta Google.

Se Gmail SMTP e Resend estiverem configurados ao mesmo tempo, o HST Alerta prioriza Gmail SMTP.


## Estado de produção
Após a validação do envio por Gmail SMTP, o canal pode ser habilitado com `HST_EMAIL_ENABLED=true`. Antes da primeira ativação real, o estado de teste deve ser limpo para que um nível 3, 4 ou 5 vigente gere o primeiro aviso operacional normalmente.
