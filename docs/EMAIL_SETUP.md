# E-mail de alertas — HST Alerta

## Canal principal
O HST Alerta utiliza Gmail SMTP como canal de produção para notificações automáticas.

## Política inicial
- Nível 1 — sem envio.
- Nível 2 — informativo opcional; inicialmente sem envio automático.
- Nível 3 — pré-alerta operacional.
- Nível 4 — alerta de gestão.
- Nível 5 — alerta máximo.
- Não repetir a mesma mensagem a cada coleta.
- Enviar novamente quando houver agravamento de nível.
- Permitir nova mensagem no mesmo nível quando o contexto predominante mudar de forma relevante.
- Após um nível notificado, enviar atualização quando houver rebaixamento. Se o nível sair da faixa do grupo, enviar uma única mensagem de melhora e então limpar o estado desse grupo.

## Secrets necessários no GitHub
Cadastrar em Settings > Secrets and variables > Actions:

- `HST_EMAIL_GRUPO_OPERACIONAL` — um ou mais e-mails do Grupo Operacional, separados por vírgula; recebe níveis 3, 4 e 5.
- `HST_EMAIL_GRUPO_GERENTES` — um ou mais e-mails do Grupo de Gerentes, separados por vírgula; recebe somente níveis 4 e 5.
- `HST_EMAIL_ENABLED` — usar `true` somente depois do teste.
- `HST_EMAIL_FROM` — opcional; se não informado, o remetente será identificado como HST Alerta usando a conta Gmail SMTP.
- `HST_EMAIL_REPLY_TO` — opcional.

## Segurança
- Não gravar senha de app, credenciais SMTP ou outros segredos em HTML, JavaScript, Python ou arquivos públicos.
- Não gravar os e-mails dos destinatários em arquivos públicos; as listas ficam em GitHub Secrets.
- Se uma credencial tiver sido compartilhada por chat, mensagem ou outro canal, revogá-la e criar outra.

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


## Gmail SMTP — produção
O HST Alerta usa Gmail SMTP para o envio automático.

Secrets:
- `HST_SMTP_USER` — conta Gmail remetente.
- `HST_SMTP_APP_PASSWORD` — senha de app de 16 caracteres criada na Conta Google.
- `HST_SMTP_HOST` — opcional; padrão `smtp.gmail.com`.
- `HST_SMTP_PORT` — opcional; padrão `465`.
- `HST_EMAIL_FROM` — opcional; pode ser omitido para usar o Gmail como remetente.
- `HST_EMAIL_REPLY_TO` — opcional.

A senha normal da conta Google não deve ser usada. A senha de app exige verificação em duas etapas na Conta Google.

## Estado de produção
Após a validação do envio por Gmail SMTP, o canal pode ser habilitado com `HST_EMAIL_ENABLED=true`. Antes da primeira ativação real, o estado de teste deve ser limpo para que um nível 3, 4 ou 5 vigente gere o primeiro aviso operacional normalmente.


## Grupos de destinatários

- Alterações nas listas de destinatários em GitHub Secrets passam a valer na próxima execução do workflow; não exigem alteração de código.
O controle de notificação é independente por grupo:

- Grupo Operacional: níveis 3, 4 e 5.
- Grupo de Gerentes: níveis 4 e 5.
- Cada Secret pode conter vários endereços separados por vírgula ou ponto e vírgula.
- Os envios são feitos individualmente para cada destinatário, evitando expor a lista de endereços aos demais.
- Se o nível cair abaixo do mínimo de um grupo, é enviada uma única mensagem de melhora com nível anterior e nível atual; após o envio, o estado desse grupo é liberado para que uma futura reentrada gere novo aviso.
