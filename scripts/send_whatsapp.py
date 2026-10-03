#!/usr/bin/env python3
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

STATUS_PATH = Path("data/status.json")
STATE_PATH = Path("data/notification_state.json")
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
        "Colocar Infraestrutura e Operações em pré-alerta",
        "Verificar água, energia, acessos e recursos de continuidade",
    ],
    4: [
        "Formalizar quadro de situação",
        "Verificar gatilhos dos planos institucionais",
        "Avaliar critérios do Comitê de Crise",
    ],
    5: [
        "Direção avaliar imediatamente os impactos reais",
        "Executar os planos aplicáveis quando seus gatilhos forem atingidos",
        "Avaliar/acionar o Comitê de Crise conforme o regimento vigente",
    ],
}

CONTEXT_ACTIONS = {
    "rain": [
        "Tratar água, energia e acessos de forma conjunta",
        "Verificar geradores, QTA, nobreaks, diesel e possível falha da rede externa",
        "Acompanhar alagamentos, enchentes, quedas de árvores e bloqueios viários",
    ],
    "wind": [
        "Verificar continuidade elétrica e possíveis interferências na rede externa",
        "Acompanhar quedas de árvores e condições dos acessos",
    ],
    "geo": [
        "Acompanhar encostas e risco de deslizamentos",
        "Verificar acessos alternativos e impactos sobre infraestrutura",
    ],
    "road": [
        "Acompanhar condições da BR-040/495 e acessos relevantes",
        "Antecipar impactos para equipes, pacientes, fornecedores e suprimentos",
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


def derive_context(data):
    sources = data.get("sources") or {}
    geo = sources.get("cemaden_geological") or {}
    hydro = sources.get("cemaden_hydrological") or {}
    inmet = sources.get("inmet_alerts") or {}
    roads = data.get("roads") or {}
    overall = data.get("overall") or {}
    pv = data.get("pluviometers") or {}
    forecast = data.get("forecast") or {}

    text_parts = [
        overall.get("reason"),
        geo.get("risk"), geo.get("message"),
        hydro.get("risk"), hydro.get("message"),
        inmet.get("risk"), inmet.get("title"),
        roads.get("alert_state"), roads.get("message"),
    ]
    text_parts.extend(overall.get("supplemental_signals") or [])
    for day in (forecast.get("days") or [])[:2]:
        text_parts.append((day or {}).get("summary"))
    text = " ".join(str(x) for x in text_parts if x).lower()

    def numeric(value):
        try:
            return float(value)
        except Exception:
            return None

    h1 = numeric((pv.get("highest_1h") or {}).get("value"))
    h24 = numeric((pv.get("highest_24h") or {}).get("value"))
    rain = (
        (hydro.get("level") or 0) > 1
        or (h1 is not None and h1 >= 20)
        or (h24 is not None and h24 >= 50)
        or any(term in text for term in (
            "chuva", "pancada", "tempestade", "precipita",
            "alag", "enchent", "inunda", "hidrol"
        ))
    )
    wind = any(term in text for term in (
        "vento forte", "rajada", "vendaval", "queda de árvore", "queda de arvore"
    ))
    geological = (geo.get("level") or 0) > 1 or any(
        term in text for term in ("desliz", "movimento de massa", "geológ", "geolog")
    )
    road = any(term in text for term in (
        "interdi", "bloque", "rodovia", "br-040", "br-495", "trânsito", "transito"
    ))

    if rain:
        return "rain", "Chuva · Água + Energia + Acessos"
    if wind:
        return "wind", "Vento · Energia + Acessos"
    if geological:
        return "geo", "Geológico · Acessos + Infraestrutura"
    if road:
        return "road", "Acessos · Logística"
    return "general", "Monitoramento geral"


def action_summary(level, context_key):
    items = []
    items.extend(CONTEXT_ACTIONS.get(context_key, []))
    items.extend(BASE_ACTIONS.get(level, []))
    unique = []
    for item in items:
        if item not in unique:
            unique.append(item)
    if not unique:
        unique = ["Manter monitoramento e seguir as ações prioritárias exibidas no HST Alerta"]
    return "; ".join(unique[:4])[:900]


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
        if level > int(last_level):
            return "escalation"
        if level < int(last_level):
            return "deescalation"
        if context_key != last_context and hours_since(state.get("last_sent_at")) >= CONTEXT_CHANGE_COOLDOWN_HOURS:
            return "context_change"
        return None

    if last_level is not None and int(last_level) >= MIN_NOTIFY_LEVEL:
        return "recovery"
    return None


def api_config():
    return {
        "recipient": os.getenv("HST_WPP_GRUPO_OPERACIONAL_TESTE", "").strip(),
        "token": os.getenv("HST_WPP_ACCESS_TOKEN", "").strip(),
        "phone_number_id": os.getenv("HST_WPP_PHONE_NUMBER_ID", "").strip(),
        "template_name": os.getenv("HST_WPP_TEMPLATE_NAME", "").strip(),
        "template_language": os.getenv("HST_WPP_TEMPLATE_LANGUAGE", "pt_BR").strip() or "pt_BR",
        "graph_version": os.getenv("HST_WPP_GRAPH_VERSION", "").strip(),
        "public_url": os.getenv("HST_ALERTA_PUBLIC_URL", "https://diogomantovani.github.io/hst-alerta/").strip(),
    }


def configured(cfg):
    return all(cfg.get(k) for k in ("recipient", "token", "phone_number_id", "template_name", "graph_version"))


def clean_text(value, max_len):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len] if text else "—"


def template_payload(cfg, level, context_label, reason, actions, updated_at, event):
    level_text = f"{level} · {LEVEL_LABELS.get(level, 'Nível HST')}"
    if event == "test":
        level_text = "TESTE · " + level_text
    elif event in {"deescalation", "recovery"}:
        level_text = "ATUALIZAÇÃO · " + level_text

    params = [
        clean_text(level_text, 80),
        clean_text(context_label, 120),
        clean_text(reason, 700),
        clean_text(actions, 850),
        clean_text(updated_at, 80),
        clean_text(cfg["public_url"], 200),
    ]
    return {
        "messaging_product": "whatsapp",
        "to": cfg["recipient"],
        "type": "template",
        "template": {
            "name": cfg["template_name"],
            "language": {"code": cfg["template_language"]},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": value} for value in params],
                }
            ],
        },
    }


def send_template(cfg, payload):
    url = f"https://graph.facebook.com/{cfg['graph_version']}/{cfg['phone_number_id']}/messages"
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if 200 <= response.status_code < 300:
        try:
            body = response.json()
            message_id = ((body.get("messages") or [{}])[0] or {}).get("id")
        except Exception:
            message_id = None
        return True, message_id, None

    error_code = None
    error_message = f"HTTP {response.status_code}"
    try:
        body = response.json()
        err = body.get("error") or {}
        error_code = err.get("code")
        error_message = err.get("message") or error_message
    except Exception:
        pass
    if cfg["recipient"]:
        error_message = error_message.replace(cfg["recipient"], "[recipient]")
    if cfg["token"]:
        error_message = error_message.replace(cfg["token"], "[token]")
    return False, None, clean_text(f"{error_code or ''} {error_message}", 500)


def update_public_status(data, state, cfg, enabled):
    notifications = data.setdefault("notifications", {})
    item = notifications.setdefault("group_operational_test", {})
    item.update({
        "channel": "whatsapp",
        "levels": [3, 4, 5],
        "recipient_configured": bool(cfg["recipient"]),
        "recipient_masked": ("•••• " + cfg["recipient"][-4:]) if cfg["recipient"] else None,
        "provider_configured": configured(cfg),
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

    cfg = api_config()
    enabled = env_bool("HST_WPP_ENABLED", False)
    force_test = env_bool("HST_WPP_FORCE_TEST", False)

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
        print(f"WHATSAPP_NO_EVENT level={level} context={context_key}")
        return

    if not configured(cfg):
        state["last_event"] = event
        state["last_result"] = "not_configured"
        state["last_error"] = "Credenciais da WhatsApp Cloud API ainda incompletas."
        update_public_status(data, state, cfg, enabled)
        save_json(STATE_PATH, state)
        save_json(STATUS_PATH, data)
        print(f"WHATSAPP_NOT_CONFIGURED event={event} level={level}")
        return

    if not enabled and not force_test:
        state["last_event"] = event
        state["last_result"] = "disabled"
        state["last_error"] = None
        update_public_status(data, state, cfg, enabled)
        save_json(STATE_PATH, state)
        save_json(STATUS_PATH, data)
        print(f"WHATSAPP_DISABLED event={event} level={level}")
        return

    reason = (data.get("overall") or {}).get("reason") or "Atualização do monitoramento HST."
    actions = action_summary(level, context_key)
    updated_at = data.get("generated_at") or now_iso()
    payload = template_payload(cfg, level, context_label, reason, actions, updated_at, event)

    state["last_attempt_at"] = now_iso()
    state["last_event"] = event
    ok, message_id, error = send_template(cfg, payload)

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
        print(f"WHATSAPP_SENT event={event} level={level}")
    else:
        state["last_result"] = "failed"
        state["last_error"] = error
        print(f"WHATSAPP_FAILED event={event} level={level} error={error}")

    update_public_status(data, state, cfg, enabled)
    save_json(STATE_PATH, state)
    save_json(STATUS_PATH, data)


if __name__ == "__main__":
    main()
