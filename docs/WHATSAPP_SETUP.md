# Integração WhatsApp — HST Alerta

## Objetivo
Enviar notificações do HST Alerta ao Grupo Operacional quando houver mudança relevante de nível, sem expor números de telefone ou credenciais no repositório público.

## Política inicial
- Nível 1 — sem envio.
- Nível 2 — informativo opcional; inicialmente não enviado ao Grupo Operacional.
- Nível 3 — pré-alerta operacional.
- Nível 4 — alerta de gestão.
- Nível 5 — alerta máximo.
- Não repetir a mesma mensagem a cada coleta.
- Enviar nova mensagem quando o nível mudar.
- Permitir nova mensagem no mesmo nível quando o contexto predominante mudar de forma relevante.
- Após nível notificado, enviar uma única atualização quando ocorrer rebaixamento.

## Destinatário de teste
O número completo é lido exclusivamente do Secret:

`HST_WPP_GRUPO_OPERACIONAL_TESTE`

O site exibe apenas os quatro últimos dígitos.

## Secrets necessários
Cadastrar em Settings > Secrets and variables > Actions:

- `HST_WPP_GRUPO_OPERACIONAL_TESTE` — já configurado.
- `HST_WPP_ACCESS_TOKEN` — token de acesso utilizado pela WhatsApp Cloud API.
- `HST_WPP_PHONE_NUMBER_ID` — ID do número remetente no WhatsApp Business Platform.
- `HST_WPP_TEMPLATE_NAME` — nome do template aprovado.
- `HST_WPP_TEMPLATE_LANGUAGE` — utilizar `pt_BR` para o template em português do Brasil.
- `HST_WPP_GRAPH_VERSION` — versão da Graph API apresentada/configurada na Meta.
- `HST_WPP_ENABLED` — usar `true` somente depois de validar a integração. Ausente ou diferente de true mantém o envio automático desativado.

## Template sugerido
Nome sugerido: `hst_alerta_nivel`

Idioma: Português (Brasil)

Corpo sugerido:

```
HST ALERTA

Nível: {{1}}
Contexto: {{2}}
Motivo: {{3}}
Ações prioritárias: {{4}}
Atualização: {{5}}
Painel: {{6}}
```

Os seis parâmetros são preenchidos automaticamente pelo HST Alerta.

## Funcionamento técnico
O workflow principal:
1. coleta as fontes;
2. calcula o Nível HST;
3. valida o site;
4. avalia se existe evento de notificação;
5. envia o template quando a integração estiver completa e habilitada;
6. registra o estado da notificação;
7. persiste histórico e publica o site.

O arquivo `data/notification_state.json` é usado para evitar mensagens repetidas e permitir reenvio em caso de falha antes de uma confirmação de envio.

## Segurança
- Não gravar token, Phone Number ID ou telefone completo em HTML, JavaScript ou arquivos públicos.
- Não imprimir credenciais nos logs.
- Manter o envio automático desativado durante a configuração inicial.
- Utilizar credencial adequada para operação contínua da aplicação, seguindo as orientações vigentes da Meta.
