#!/usr/bin/env python3
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
CITY = "PETRÓPOLIS"
BASE = "https://painelcemadenrj.defesacivil.rj.gov.br/monitoramento/v2/municipio/"
PREVIOUS_URL = "https://diogomantovani.github.io/hst-alerta/data/status.json"
OUT = os.path.join("data", "status.json")

RISK_TO_LEVEL = {
    "MUITO BAIXO": 1,
    "BAIXO": 2,
    "MODERADO": 3,
    "ALTO": 4,
    "MUITO ALTO": 5,
}

LEVEL_LABELS = {
    1: "Vigilância",
    2: "Observação",
    3: "Atenção",
    4: "Alerta",
    5: "Alerta Máximo",
}

def norm(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).upper().strip()

def load_previous():
    # Prefer the last deployed snapshot, which persists across ephemeral Actions runners.
    try:
        r = requests.get(PREVIOUS_URL, timeout=12, headers={"User-Agent": "HST-Alerta/1.0"})
        if r.ok:
            return r.json()
    except Exception:
        pass

    try:
        with open(OUT, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "schema_version": 1,
            "generated_at": None,
            "overall": {"level": 1, "label": "Vigilância", "reason": "Aguardando primeira coleta oficial."},
            "sources": {},
        }

def parse_update(value):
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y %H:%M:%S").replace(tzinfo=TZ)
    except Exception:
        return None

def fetch_risk(action, key, label, previous):
    url = BASE + f"?action={action}"
    prev = (previous.get("sources") or {}).get(key, {})
    now = datetime.now(TZ)

    try:
        response = requests.get(
            url,
            timeout=25,
            headers={
                "User-Agent": "Mozilla/5.0 HST-Alerta/1.0 (+Hospital Santa Teresa; monitoring)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        match = None
        for tr in soup.find_all("tr"):
            cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if cells and norm(cells[0]) == norm(CITY):
                match = cells
                break

        if not match or len(match) < 4:
            raise RuntimeError(f"{CITY} não encontrado na tabela oficial")

        risk = match[2].strip().upper()
        updated_text = match[3].strip()
        if risk not in RISK_TO_LEVEL:
            raise RuntimeError(f"Risco não reconhecido: {risk}")

        observed = parse_update(updated_text)
        age_hours = None
        health = "ok"
        if observed:
            age_hours = round((now - observed).total_seconds() / 3600, 1)
            # Keep the official risk active, but flag stale source data visibly.
            if age_hours > 12:
                health = "stale"

        return {
            "name": label,
            "provider": "CEMADEN-RJ / Defesa Civil RJ",
            "status": health,
            "risk": risk.title(),
            "level": RISK_TO_LEVEL[risk],
            "official_updated_at": observed.isoformat() if observed else updated_text,
            "age_hours": age_hours,
            "collected_at": now.isoformat(),
            "url": url,
            "error": None,
        }

    except Exception as exc:
        # Conservative fallback: never interpret failure as low/no risk.
        fallback = dict(prev) if prev else {}
        fallback.update({
            "name": label,
            "provider": "CEMADEN-RJ / Defesa Civil RJ",
            "status": "unavailable",
            "collected_at": now.isoformat(),
            "url": url,
            "error": str(exc)[:300],
        })
        if "level" not in fallback:
            fallback["level"] = None
            fallback["risk"] = "Indisponível"
        return fallback

def main():
    os.makedirs("data", exist_ok=True)
    previous = load_previous()

    geological = fetch_risk(1, "cemaden_geological", "Deslizamento", previous)
    hydrological = fetch_risk(2, "cemaden_hydrological", "Hidrológico", previous)

    usable = [s for s in (geological, hydrological) if isinstance(s.get("level"), int)]
    if usable:
        overall_level = max(s["level"] for s in usable)
    else:
        overall_level = int((previous.get("overall") or {}).get("level") or 1)

    # Geological and hydrological panels are the same provider; they do NOT
    # count as two independent sources for the future multi-source escalation rule.
    top = [s for s in usable if s.get("level") == overall_level]
    reasons = ", ".join(f'{s["name"]}: {s.get("risk")}' for s in top) or "Sem nova leitura válida."
    unavailable = [s["name"] for s in (geological, hydrological) if s.get("status") == "unavailable"]
    stale = [s["name"] for s in (geological, hydrological) if s.get("status") == "stale"]
    if unavailable:
        reasons += ". Fonte(s) indisponível(is): " + ", ".join(unavailable) + "; mantido último valor válido quando disponível."
    if stale:
        reasons += ". Atenção: atualização oficial antiga em " + ", ".join(stale) + "."

    now = datetime.now(TZ)
    payload = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "location": {"city": "Petrópolis", "state": "RJ", "country": "Brasil"},
        "overall": {
            "level": overall_level,
            "label": LEVEL_LABELS[overall_level],
            "reason": reasons,
            "rule": "Maior nível válido entre riscos CEMADEN-RJ. Falha de fonte não reduz automaticamente o nível.",
        },
        "sources": {
            "cemaden_geological": geological,
            "cemaden_hydrological": hydrological,
        },
        "integrations": {
            "cemaden_rj": "active",
            "inmet": "pending",
            "pluviometers": "pending",
            "defesa_civil": "pending",
            "roads": "pending",
            "utilities": "pending",
        },
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
