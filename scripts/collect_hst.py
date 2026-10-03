#!/usr/bin/env python3
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
CITY = "PETRÓPOLIS"
OUT = os.path.join("data", "status.json")
PREVIOUS_URL = "https://diogomantovani.github.io/hst-alerta/data/status.json"

CEMADEN_BASE = "https://painelcemadenrj.defesacivil.rj.gov.br/monitoramento/v2/municipio/"
INMET_WEATHER = "https://apitempo.inmet.gov.br/estacao/{start}/{end}/A610"
INMET_ALERTS = "https://apiprevmet3.inmet.gov.br/avisos/ativos"

RISK_TO_LEVEL = {"MUITO BAIXO":1,"BAIXO":2,"MODERADO":3,"ALTO":4,"MUITO ALTO":5}
LEVEL_LABELS = {1:"Vigilância",2:"Observação",3:"Atenção",4:"Alerta",5:"Alerta Máximo"}
ALERT_LEVEL = {"SEM AVISO":1,"AMARELO":2,"LARANJA":3,"VERMELHO":4}

def norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in value if not unicodedata.combining(c)).upper().strip()

def get_json(url, timeout=25):
    r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
    r.raise_for_status()
    return r.json()

def load_previous():
    try:
        return get_json(PREVIOUS_URL, timeout=12)
    except Exception:
        try:
            with open(OUT,"r",encoding="utf-8") as f: return json.load(f)
        except Exception:
            return {"schema_version":2,"generated_at":None,"overall":{"level":1,"label":"Vigilância","reason":"Aguardando primeira coleta."},"sources":{},"weather":{}}

def parse_dt(value):
    if not value: return None
    text=str(value).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S","%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d %H:%M:%S"):
        try:
            d=datetime.strptime(text,fmt)
            return d if d.tzinfo else d.replace(tzinfo=TZ)
        except Exception: pass
    try:
        d=datetime.fromisoformat(text.replace("Z","+00:00"))
        return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
    except Exception: return None

def safe_float(v):
    if v in (None,"","null","None"): return None
    try: return float(str(v).replace(",","."))
    except Exception: return None

def fetch_cemaden(action,key,label,previous):
    url=CEMADEN_BASE+f"?action={action}"
    prev=(previous.get("sources") or {}).get(key,{})
    now=datetime.now(TZ)
    try:
        r=requests.get(url,timeout=25,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0","Accept":"text/html,application/xhtml+xml"})
        r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        match=None
        for tr in soup.find_all("tr"):
            cells=[re.sub(r"\s+"," ",td.get_text(" ",strip=True)) for td in tr.find_all(["td","th"])]
            if cells and norm(cells[0])==norm(CITY):
                match=cells; break
        if not match or len(match)<4: raise RuntimeError(f"{CITY} não encontrado")
        risk=match[2].strip().upper()
        if risk not in RISK_TO_LEVEL: raise RuntimeError(f"Risco não reconhecido: {risk}")
        observed=parse_dt(match[3])
        age=round((now-observed).total_seconds()/3600,1) if observed else None
        status="stale" if age is not None and age>12 else "ok"
        return {"name":label,"provider":"CEMADEN-RJ / Defesa Civil RJ","status":status,"risk":risk.title(),"level":RISK_TO_LEVEL[risk],"official_updated_at":observed.isoformat() if observed else match[3],"age_hours":age,"collected_at":now.isoformat(),"url":url,"error":None}
    except Exception as exc:
        fallback=dict(prev) if prev else {}
        fallback.update({"name":label,"provider":"CEMADEN-RJ / Defesa Civil RJ","status":"unavailable","collected_at":now.isoformat(),"url":url,"error":str(exc)[:300]})
        if "level" not in fallback: fallback.update({"level":None,"risk":"Indisponível"})
        return fallback

def observation_datetime(row):
    date=row.get("DT_MEDICAO") or row.get("data") or row.get("DATA")
    hour=row.get("HR_MEDICAO") or row.get("hora") or row.get("HORA")
    if date:
        ds=str(date).strip()
        if hour not in (None,""):
            hs=str(hour).strip().zfill(4)
            try:
                d=datetime.strptime(ds+" "+hs[:2]+":"+hs[2:4],"%Y-%m-%d %H:%M")
                # INMET automatic station timestamps are commonly published in UTC.
                return d.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
            except Exception: pass
        d=parse_dt(ds)
        if d: return d
    return None

def fetch_inmet_weather(previous):
    prev=previous.get("weather") or {}
    now=datetime.now(TZ)
    start=(now-timedelta(days=1)).strftime("%Y-%m-%d")
    end=now.strftime("%Y-%m-%d")
    url=INMET_WEATHER.format(start=start,end=end)
    try:
        data=get_json(url)
        if isinstance(data,dict):
            candidates=data.get("dados") or data.get("data") or data.get("results") or []
        else:
            candidates=data
        if not isinstance(candidates,list) or not candidates:
            raise RuntimeError("API do INMET retornou lista vazia")
        rows=[r for r in candidates if isinstance(r,dict)]
        if not rows: raise RuntimeError("Sem observações válidas do INMET")
        rows.sort(key=lambda r: observation_datetime(r) or datetime.min.replace(tzinfo=TZ))
        row=rows[-1]
        obs=observation_datetime(row)
        age=round((now-obs).total_seconds()/3600,1) if obs else None
        status="stale" if age is not None and age>6 else "ok"
        return {
          "provider":"INMET","station":{"code":"A610","name":"Pico do Couto","type":"referência regional"},
          "status":status,
          "observed_at":obs.isoformat() if obs else None,
          "age_hours":age,
          "temperature_c":safe_float(row.get("TEM_INS") or row.get("temperatura")),
          "humidity_pct":safe_float(row.get("UMD_INS") or row.get("umidade")),
          "rain_1h_mm":safe_float(row.get("CHUVA") or row.get("precipitacao")),
          "wind_gust_ms":safe_float(row.get("VEN_RAJ") or row.get("rajada")),
          "pressure_hpa":safe_float(row.get("PRE_INS") or row.get("pressao")),
          "collected_at":now.isoformat(),"url":url,"error":None
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({"provider":"INMET","station":{"code":"A610","name":"Pico do Couto","type":"referência regional"},"status":"unavailable","collected_at":now.isoformat(),"url":url,"error":str(exc)[:300]})
        return fallback

def severity_from_alert(item):
    blob=norm(json.dumps(item,ensure_ascii=False))
    for color in ("VERMELHO","LARANJA","AMARELO"):
        if color in blob: return color
    # Fallback to common severity wording.
    if "GRANDE PERIGO" in blob or "EXTREMO" in blob: return "VERMELHO"
    if re.search(r"\bPERIGO\b",blob): return "LARANJA"
    if "PERIGO POTENCIAL" in blob or "POTENCIAL" in blob: return "AMARELO"
    return None

def alert_times(item):
    blob=item if isinstance(item,dict) else {}
    start=blob.get("inicio") or blob.get("Início") or blob.get("start") or blob.get("data_inicio")
    end=blob.get("fim") or blob.get("Fim") or blob.get("end") or blob.get("data_fim")
    return start,end

def fetch_inmet_alerts(previous):
    prev=(previous.get("sources") or {}).get("inmet_alerts",{})
    now=datetime.now(TZ)
    try:
        data=get_json(INMET_ALERTS)
        items=[]
        if isinstance(data,list): items=data
        elif isinstance(data,dict):
            for key in ("avisos","data","results","features"):
                if isinstance(data.get(key),list):
                    items=data[key]; break
            if not items:
                # Some INMET endpoints return a mapping keyed by alert id.
                vals=[v for v in data.values() if isinstance(v,dict)]
                if vals: items=vals
        matching=[]
        for item in items:
            blob=norm(json.dumps(item,ensure_ascii=False))
            if "PETROPOLIS" in blob and ("RJ" in blob or "RIO DE JANEIRO" in blob):
                sev=severity_from_alert(item)
                matching.append((ALERT_LEVEL.get(sev,1),sev,item))
        matching.sort(key=lambda x:x[0],reverse=True)
        if matching:
            level,sev,item=matching[0]
            start,end=alert_times(item if isinstance(item,dict) else {})
            title=(item.get("evento") or item.get("aviso") or item.get("descricao") or item.get("headline") or "Aviso meteorológico") if isinstance(item,dict) else "Aviso meteorológico"
            return {"name":"Meteorológico","provider":"INMET","status":"ok","risk":sev.title() if sev else "Aviso ativo","level":level,"title":str(title)[:250],"active_count":len(matching),"starts_at":start,"ends_at":end,"collected_at":now.isoformat(),"url":INMET_ALERTS,"error":None}
        return {"name":"Meteorológico","provider":"INMET","status":"ok","risk":"Sem aviso","level":1,"title":"Nenhum aviso ativo identificado para Petrópolis/RJ","active_count":0,"starts_at":None,"ends_at":None,"collected_at":now.isoformat(),"url":INMET_ALERTS,"error":None}
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({"name":"Meteorológico","provider":"INMET","status":"unavailable","collected_at":now.isoformat(),"url":INMET_ALERTS,"error":str(exc)[:300]})
        if "level" not in fallback: fallback.update({"level":None,"risk":"Indisponível","title":"Fonte de avisos INMET indisponível"})
        return fallback

def main():
    os.makedirs("data",exist_ok=True)
    previous=load_previous()
    geo=fetch_cemaden(1,"cemaden_geological","Deslizamento",previous)
    hydro=fetch_cemaden(2,"cemaden_hydrological","Hidrológico",previous)
    weather=fetch_inmet_weather(previous)
    inmet=fetch_inmet_alerts(previous)

    usable=[s for s in (geo,hydro,inmet) if isinstance(s.get("level"),int)]
    overall=max([s["level"] for s in usable], default=int((previous.get("overall") or {}).get("level") or 1))

    # Optional combined-source escalation: only independent providers count.
    cemaden_level=max([s.get("level") or 0 for s in (geo,hydro)])
    inmet_level=inmet.get("level") if isinstance(inmet.get("level"),int) else 0
    escalated=False
    if cemaden_level>=3 and inmet_level>=3:
        overall=min(5,max(cemaden_level,inmet_level)+1)
        escalated=True

    top=[]
    for s in (geo,hydro,inmet):
        if isinstance(s.get("level"),int) and s["level"]>=max(1,overall-(1 if escalated else 0)):
            top.append(f'{s["name"]}: {s.get("risk")}')
    reason=", ".join(top) or "Sem nova leitura válida."
    if escalated: reason += ". Escalada por duas fontes independentes em nível 3 ou superior."
    unavailable=[s["name"] for s in (geo,hydro,inmet) if s.get("status")=="unavailable"]
    stale=[s["name"] for s in (geo,hydro) if s.get("status")=="stale"]
    if unavailable: reason += ". Fonte(s) indisponível(is): "+", ".join(unavailable)+"; último valor válido preservado quando disponível."
    if stale: reason += ". Atenção: atualização oficial antiga em "+", ".join(stale)+"."

    now=datetime.now(TZ)
    payload={
      "schema_version":2,
      "generated_at":now.isoformat(),
      "location":{"city":"Petrópolis","state":"RJ","country":"Brasil"},
      "overall":{"level":overall,"label":LEVEL_LABELS[overall],"reason":reason,"rule":"Maior nível válido entre CEMADEN-RJ e INMET; se ambas as fontes independentes estiverem em nível >=3, escalada configurada de +1."},
      "sources":{"cemaden_geological":geo,"cemaden_hydrological":hydro,"inmet_alerts":inmet},
      "weather":weather,
      "integrations":{"cemaden_rj":"active","inmet":"active","pluviometers":"pending","defesa_civil":"pending","roads":"pending","utilities":"pending"}
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
