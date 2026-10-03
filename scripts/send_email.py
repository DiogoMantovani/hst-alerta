#!/usr/bin/env python3
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

STATUS_PATH = Path("data/status.json")
STATE_PATH = Path("data/email_notification_state.json")
MIN_NOTIFY_LEVEL = 3
CONTEXT_CHANGE_COOLDOWN_HOURS = 3

LEVEL_LABELS = {
    1: "Vigilância",
    2: "Observação",
    3: "Atenção",
    4: "Alerta",
    5: "Alerta Máximo",
}

BASE_ACTIONS = {
    3: [
        "Colocar Infraestrutura e Operações em pré-alerta.",
        "Verificar água, energia, acessos e recursos de continuidade.",
    ],
    4: [
        "Formalizar quadro de situação.",
        "Verificar gatilhos dos planos institucionais.",
        "Avaliar critérios institucionais para acionamento do Comitê de Crise.",
    ],
    5: [
        "Direção deve avaliar imediatamente os impactos reais.",
        "Executar os planos aplicáveis quando seus gatilhos internos forem atingidos.",
        "Avaliar e, quando houver critério institucional, acionar o Comitê de Crise.",
    ],
}

CONTEXT_ACTIONS = {
    "rain": [
        "Tratar água, energia e acessos de forma conjunta.",
        "Verificar geradores, QTA, nobreaks, diesel e possível falha da rede elétrica externa.",
        "Acompanhar alagamentos, enchentes, quedas de árvores e bloqueios viários.",
    ],
    "wind": [
        "Verificar continuidade elétrica e possíveis interferências na rede externa.",
        "Acompanhar quedas de árvores e condições dos acessos.",
    ],
    "geo": [
        "Acompanhar encostas e risco de deslizamentos.",
        "Verificar acessos alternativos e impactos sobre infraestrutura.",
    ],
    "road": [
        "Acompanhar condições da BR-040/495 e demais acessos relevantes.",
        "Antecipar impactos para equipes, pacientes, fornecedores e suprimentos.",
    ],
}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_text(value, max_len=2000):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len] if text else "—"


def numeric(value):
    try:
        return float(value)
    except Exception:
        return None


def derive_context(data):
    sources = data.get("sources") or {}
    geo = sources.get("cemaden_geological") or {}
    hydro = sources.get("cemaden_hydrological") or {}
    inmet = sources.get("inmet_alerts") or {}
    roads = data.get("roads") or {}
    overall = data.get("overall") or {}
    pv = data.get("pluviometers") or {}
    forecast = data.get("forecast") or {}

    parts = [
        overall.get("reason"),
        geo.get("risk"), geo.get("message"),
        hydro.get("risk"), hydro.get("message"),
        inmet.get("risk"), inmet.get("title"),
        roads.get("alert_state"), roads.get("message"),
    ]
    parts.extend(overall.get("supplemental_signals") or [])
    for day in (forecast.get("days") or [])[:2]:
        parts.append((day or {}).get("summary"))
    text = " ".join(str(x) for x in parts if x).lower()

    h1 = numeric((pv.get("highest_1h") or {}).get("value"))
    h24 = numeric((pv.get("highest_24h") or {}).get("value"))

    rain = (
        (hydro.get("level") or 0) > 1
        or (h1 is not None and h1 >= 20)
        or (h24 is not None and h24 >= 50)
        or any(t in text for t in ("chuva", "pancada", "tempestade", "precipita", "alag", "enchent", "inunda", "hidrol"))
    )
    wind = any(t in text for t in ("vento forte", "rajada", "vendaval", "queda de árvore", "queda de arvore"))
    geological = (geo.get("level") or 0) > 1 or any(t in text for t in ("desliz", "movimento de massa", "geológ", "geolog"))
    road = any(t in text for t in ("interdi", "bloque", "rodovia", "br-040", "br-495", "trânsito", "transito"))

    if rain:
        return "rain", "Chuva · Água + Energia + Acessos"
    if wind:
        return "wind", "Vento · Energia + Acessos"
    if geological:
        return "geo", "Geológico · Acessos + Infraestrutura"
    if road:
        return "road", "Acessos · Logística"
    return "general", "Monitoramento geral"


def actions_for(level, context_key):
    items = []
    items.extend(CONTEXT_ACTIONS.get(context_key, []))
    items.extend(BASE_ACTIONS.get(level, []))
    unique = []
    for item in items:
        if item not in unique:
            unique.append(item)
    if not unique:
        unique = ["Manter monitoramento e seguir as ações prioritárias exibidas no HST Alerta."]
    return unique[:5]


def hours_since(value):
    if not value:
        return 9999
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 9999


def determine_event(level, context_key, state, force_test=False):
    if force_test:
        return "test"

    last_level = state.get("last_notified_level")
    last_context = state.get("last_notified_context")

    if level >= MIN_NOTIFY_LEVEL:
        if last_level is None:
            return "entry"
        last_level = int(last_level)
        if level > last_level:
            return "escalation"
        if level < last_level:
            return "deescalation"
        if context_key != last_context and hours_since(state.get("last_sent_at")) >= CONTEXT_CHANGE_COOLDOWN_HOURS:
            return "context_change"
        return None

    if last_level is not None and int(last_level) >= MIN_NOTIFY_LEVEL:
        return "recovery"
    return None


def config():
    return {
        "recipient": os.getenv("HST_EMAIL_GRUPO_OPERACIONAL", "").strip(),
        "api_key": os.getenv("RESEND_API_KEY", "").strip(),
        "from_email": os.getenv("HST_EMAIL_FROM", "").strip() or "HST Alerta <onboarding@resend.dev>",
        "reply_to": os.getenv("HST_EMAIL_REPLY_TO", "").strip(),
        "public_url": os.getenv("HST_ALERTA_PUBLIC_URL", "https://diogomantovani.github.io/hst-alerta/").strip(),
    }


def configured(cfg):
    return all(cfg.get(k) for k in ("recipient", "api_key", "from_email"))


def event_label(event):
    return {
        "test": "TESTE",
        "entry": "NOVO ALERTA",
        "escalation": "AGRAVAMENTO",
        "deescalation": "ATUALIZAÇÃO",
        "context_change": "MUDANÇA DE CONTEXTO",
        "recovery": "MELHORA DO CENÁRIO",
    }.get(event, "ATUALIZAÇÃO")


def build_message(data, level, context_key, context_label, event, cfg):
    overall = data.get("overall") or {}
    reason = clean_text(overall.get("reason") or "Atualização do monitoramento HST.", 1400)
    actions = actions_for(level, context_key)
    generated_at = data.get("generated_at") or now_iso()
    label = LEVEL_LABELS.get(level, "Nível HST")
    event_text = event_label(event)

    subject = f"HST Alerta | {event_text} | Nível {level} · {label}"

    action_html = "".join(f"<li style=\"margin:0 0 8px\">{html.escape(a)}</li>" for a in actions)
    action_text = "\n".join(f"- {a}" for a in actions)

    body_html = f"""<!doctype html>
<html lang="pt-BR">
<body style="margin:0;background:#f4f7f7;font-family:Arial,Helvetica,sans-serif;color:#173336">
  <div style="max-width:680px;margin:24px auto;background:#ffffff;border:1px solid #dce7e7;border-radius:14px;overflow:hidden">
    <div style="padding:22px 26px;background:#0b6f70;color:#ffffff">
      <div style="font-size:12px;font-weight:700;letter-spacing:.08em">{html.escape(event_text)}</div>
      <div style="font-size:25px;font-weight:800;margin-top:6px">HST ALERTA · NÍVEL {level} · {html.escape(label)}</div>
    </div>
    <div style="padding:26px">
      <div style="font-size:13px;color:#617779;margin-bottom:5px">CONTEXTO OPERACIONAL</div>
      <div style="font-size:19px;font-weight:700;margin-bottom:20px">{html.escape(context_label)}</div>
      <div style="font-size:13px;color:#617779;margin-bottom:5px">MOTIVO</div>
      <div style="line-height:1.55;margin-bottom:22px">{html.escape(reason)}</div>
      <div style="font-size:13px;color:#617779;margin-bottom:5px">AÇÕES PRIORITÁRIAS</div>
      <ul style="padding-left:20px;line-height:1.45;margin-top:8px">{action_html}</ul>
      <div style="margin-top:24px;padding:14px 16px;background:#f1f7f7;border-radius:10px">
        <strong>Atualização:</strong> {html.escape(clean_text(generated_at,120))}
      </div>
      <div style="margin-top:22px">
        <a href="{html.escape(cfg['public_url'], quote=True)}" style="display:inline-block;background:#0b6f70;color:#ffffff;text-decoration:none;font-weight:700;padding:11px 16px;border-radius:8px">Abrir HST Alerta</a>
      </div>
      <div style="margin-top:24px;font-size:12px;line-height:1.5;color:#65787a">
        Mensagem automática de apoio à decisão. Os documentos institucionais vigentes e as fontes oficiais permanecem soberanos.
      </div>
    </div>
  </div>
</body>
</html>"""

    body_text = f"""HST ALERTA — {event_text}
Nível: {level} · {label}
Contexto: {context_label}

Motivo:
{reason}

Ações prioritárias:
{action_text}

Atualização: {generated_at}
Painel: {cfg['public_url']}

Mensagem automática de apoio à decisão. Os documentos institucionais vigentes e as fontes oficiais permanecem soberanos.
"""
    return subject, body_html, body_text


def send_email(cfg, subject, body_html, body_text):
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": cfg["from_email"],
        "to": [cfg["recipient"]],
        "subject": subject,
        "html": body_html,
        "text": body_text,
    }
    if cfg.get("reply_to"):
        payload["reply_to"] = cfg["reply_to"]

    try:
        response = requests.post("https://api.resend.com/emails", headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        return False, None, f"Falha de conexão com o Resend: {exc.__class__.__name__}"

    if 200 <= response.status_code < 300:
        try:
            message_id = (response.json() or {}).get("id")
        except Exception:
            message_id = None
        return True, message_id, None

    error = f"HTTP {response.status_code}"
    try:
        body = response.json() or {}
        error = clean_text(body.get("message") or body.get("name") or error, 500)
    except Exception:
        pass
    return False, None, error


def update_public_status(data, state, cfg, enabled):
    notifications = data.setdefault("notifications", {})
    item = notifications.setdefault("group_operational_email", {})
    item.update({
        "channel": "email",
        "levels": [3, 4, 5],
        "recipient_configured": bool(cfg["recipient"]),
        "provider_configured": bool(cfg["api_key"]),
        "automatic_sending_enabled": bool(enabled),
        "status": (
            "active" if configured(cfg) and enabled
            else "ready_disabled" if configured(cfg)
            else "awaiting_provider" if cfg["recipient"]
            else "recipient_missing"
        ),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_sent_at": state.get("last_sent_at"),
        "last_event": state.get("last_event"),
        "last_result": state.get("last_result"),
        "last_notified_level": state.get("last_notified_level"),
    })
    notifications.pop("group_operational_test", None)


def main():
    data = load_json(STATUS_PATH, {})
    state = load_json(STATE_PATH, {
        "schema_version": 1,
        "last_notified_level": None,
        "last_notified_context": None,
        "last_sent_at": None,
        "last_attempt_at": None,
        "last_event": None,
        "last_result": None,
        "last_error": None,
        "last_message_id": None,
    })

    cfg = config()
    enabled = env_bool("HST_EMAIL_ENABLED", False)
    force_test = env_bool("HST_EMAIL_FORCE_TEST", False)

    try:
        level = max(1, min(5, int((data.get("overall") or {}).get("level") or 1)))
    except Exception:
        level = 1

    context_key, context_label = derive_context(data)
    event = determine_event(level, context_key, state, force_test=force_test)

    state["last_seen_level"] = level
    state["last_seen_context"] = context_key
    state["updated_at"] = now_iso()

    if not event:
        state["last_result"] = "no_event"
        update_public_status(data, state, cfg, enabled)
        save_json(STATE_PATH, state)
        save_json(STATUS_PATH, data)
        print(f"EMAIL_NO_EVENT level={level} context={context_key}")
        return

    state["last_event"] = event

    if not configured(cfg):
        state["last_result"] = "not_configured"
        state["last_error"] = "Configuração de e-mail ainda incompleta."
        update_public_status(data, state, cfg, enabled)
        save_json(STATE_PATH, state)
        save_json(STATUS_PATH, data)
        print(f"EMAIL_NOT_CONFIGURED event={event} level={level}")
        return

    if not enabled and not force_test:
        state["last_result"] = "disabled"
        state["last_error"] = None
        update_public_status(data, state, cfg, enabled)
        save_json(STATE_PATH, state)
        save_json(STATUS_PATH, data)
        print(f"EMAIL_DISABLED event={event} level={level}")
        return

    subject, body_html, body_text = build_message(
        data, level, context_key, context_label, event, cfg
    )
    state["last_attempt_at"] = now_iso()
    ok, message_id, error = send_email(cfg, subject, body_html, body_text)

    if ok:
        state["last_result"] = "sent"
        state["last_error"] = None
        state["last_message_id"] = message_id
        state["last_sent_at"] = now_iso()
        if event == "recovery" or level < MIN_NOTIFY_LEVEL:
            state["last_notified_level"] = None
            state["last_notified_context"] = None
        else:
            state["last_notified_level"] = level
            state["last_notified_context"] = context_key
        print(f"EMAIL_SENT event={event} level={level}")
    else:
        state["last_result"] = "failed"
        state["last_error"] = clean_text(error, 500)
        print(f"EMAIL_FAILED event={event} level={level} error={state['last_error']}")

    update_public_status(data, state, cfg, enabled)
    save_json(STATE_PATH, state)
    save_json(STATUS_PATH, data)


if __name__ == "__main__":
    main()
