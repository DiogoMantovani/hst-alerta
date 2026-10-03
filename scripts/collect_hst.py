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
HISTORY_OUT = os.path.join("data", "history.json")
HISTORY_INDEX_OUT = os.path.join("data", "history_index.json")
ARCHIVE_DIR = os.path.join("data", "archive")
PREVIOUS_URL = "https://diogomantovani.github.io/hst-alerta/data/status.json"
HISTORY_URL = "https://diogomantovani.github.io/hst-alerta/data/history.json"

CEMADEN_BASE = "https://painelcemadenrj.defesacivil.rj.gov.br/monitoramento/v2/municipio/"
CEMADEN_PLUVIO = "https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RJ"
INMET_WEATHER = "https://apitempo.inmet.gov.br/estacao/{start}/{end}/A610"
INMET_ALERTS = "https://apiprevmet3.inmet.gov.br/avisos/ativos"
INMET_FORECAST = "https://apiprevmet3.inmet.gov.br/previsao/3303906"
DEFESA_CIVIL_HOME = "https://www.petropolis.rj.gov.br/pmp/index.php/defesa-civil"
DEFESA_CIVIL_TAG = "https://www.petropolis.rj.gov.br/pmp/index.php/component/tags/tag/defesa-civil"
DEFESA_CIVIL_BOLETIM = "https://www.petropolis.rj.gov.br/boletim"
DEFESA_CIVIL_WHATSAPP = "https://whatsapp.com/channel/0029VaKX3R5D38CZuMcmc03i"
ELOVIAS_HOME = "https://elovias.com.br/home"
ELOVIAS_MAP = "https://elovias.com.br/mapa"

RISK_TO_LEVEL = {"MUITO BAIXO":1,"BAIXO":2,"MODERADO":3,"ALTO":4,"MUITO ALTO":5}
LEVEL_LABELS = {1:"Vigilância",2:"Observação",3:"Atenção",4:"Alerta",5:"Alerta Máximo"}
ALERT_LEVEL = {"SEM AVISO":1,"AMARELO":2,"LARANJA":3,"VERMELHO":4}
DC_LEVEL = {"VIGILANCIA":1,"OBSERVACAO":2,"ATENCAO":3,"ALERTA":4,"ALERTA MAXIMO":5,"CRISE":5}

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
    today=now.strftime("%Y-%m-%d")
    yesterday=(now-timedelta(days=1)).strftime("%Y-%m-%d")

    # INMET has changed/retired some public API paths over time. Try the
    # documented station range path first, then the official all-stations
    # daily paths and filter A610 locally.
    candidates_urls=[
        INMET_WEATHER.format(start=yesterday,end=today),
        f"https://apitempo.inmet.gov.br/estacao/dados/{today}",
        f"https://apitempo.inmet.gov.br/estacao/dados/{yesterday}",
    ]
    errors=[]
    for url in candidates_urls:
        try:
            r=requests.get(url,timeout=18,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0","Accept":"application/json,text/plain,*/*"})
            if r.status_code==204 or not r.text.strip():
                raise RuntimeError(f"HTTP {r.status_code} sem conteúdo")
            r.raise_for_status()
            try:
                data=r.json()
            except Exception:
                raise RuntimeError(f"Resposta não JSON (HTTP {r.status_code}, content-type={r.headers.get('content-type')})")

            if isinstance(data,dict):
                rows=data.get("dados") or data.get("data") or data.get("results") or []
                if not rows:
                    # Some APIs may key records by station/time.
                    rows=[v for v in data.values() if isinstance(v,dict)]
            else:
                rows=data
            if not isinstance(rows,list):
                raise RuntimeError("Formato de dados inesperado")

            valid=[]
            for row in rows:
                if not isinstance(row,dict): continue
                code=norm(row.get("CD_ESTACAO") or row.get("codigo") or row.get("station"))
                name=norm(row.get("DC_NOME") or row.get("nome") or row.get("station_name"))
                if code=="A610" or "PICO DO COUTO" in name:
                    valid.append(row)
            # The station-specific endpoint may omit CD_ESTACAO in every row.
            if not valid and "A610" in url and rows:
                valid=[r for r in rows if isinstance(r,dict)]

            if not valid:
                raise RuntimeError("A610 não encontrada na resposta")

            valid.sort(key=lambda row: observation_datetime(row) or datetime.min.replace(tzinfo=TZ))
            row=valid[-1]
            obs=observation_datetime(row)
            age=round((now-obs).total_seconds()/3600,1) if obs else None
            status="stale" if age is not None and age>6 else "ok"
            return {
              "provider":"INMET",
              "station":{"code":"A610","name":"Pico do Couto","type":"referência regional"},
              "status":status,
              "observed_at":obs.isoformat() if obs else None,
              "age_hours":age,
              "temperature_c":safe_float(row.get("TEM_INS") if row.get("TEM_INS") is not None else row.get("temperatura")),
              "humidity_pct":safe_float(row.get("UMD_INS") if row.get("UMD_INS") is not None else row.get("umidade")),
              "rain_1h_mm":safe_float(row.get("CHUVA") if row.get("CHUVA") is not None else row.get("precipitacao")),
              "wind_gust_ms":safe_float(row.get("VEN_RAJ") if row.get("VEN_RAJ") is not None else row.get("rajada")),
              "pressure_hpa":safe_float(row.get("PRE_INS") if row.get("PRE_INS") is not None else row.get("pressao")),
              "collected_at":now.isoformat(),"url":url,"error":None
            }
        except Exception as exc:
            errors.append(url+" -> "+str(exc)[:180])

    fallback=dict(prev)
    fallback.update({
      "provider":"INMET",
      "station":{"code":"A610","name":"Pico do Couto","type":"referência regional"},
      "status":"unavailable",
      "collected_at":now.isoformat(),
      "url":candidates_urls[0],
      "error":" | ".join(errors)[:900]
    })
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

def fetch_cemaden_pluviometers(previous):
    prev=previous.get("pluviometers") or {}
    now=datetime.now(TZ)
    try:
        r=requests.get(CEMADEN_PLUVIO,timeout=25,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0","Accept":"application/json,text/plain,*/*"})
        r.raise_for_status()
        data=json.loads(r.text)
        if not isinstance(data,list):
            raise RuntimeError("Formato inesperado do endpoint público de pluviômetros")

        stations=[]
        for row in data:
            if not isinstance(row,dict) or str(row.get("codibge"))!="3303906":
                continue
            raw_dt=str(row.get("datahoraUltimovalor") or "").strip()
            observed=None
            try:
                observed=datetime.strptime(raw_dt,"%d/%m/%y %H:%M").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
            except Exception:
                pass
            age=round((now-observed).total_seconds()/3600,1) if observed else None
            # A reading materially ahead of the collector clock is kept as raw
            # evidence, but is not treated as a fresh/valid reading for summaries.
            # Small clock skew up to 15 minutes is tolerated.
            if age is None:
                health="stale"
            elif age < -0.25:
                health="time_anomaly"
            elif age <= 2:
                health="ok"
            else:
                health="stale"
            def mm(key):
                v=row.get(key)
                return safe_float(v) if v not in ("-",None,"") else None
            stations.append({
                "id":row.get("idestacao"),
                "name":row.get("nomeestacao") or "Estação sem nome",
                "status":health,
                "raw_timestamp":raw_dt or None,
                "observed_at":observed.isoformat() if observed else raw_dt or None,
                "age_hours":age,
                "last_mm":mm("ultimovalor"),
                "acc1h_mm":mm("acc1hr"),
                "acc3h_mm":mm("acc3hr"),
                "acc6h_mm":mm("acc6hr"),
                "acc12h_mm":mm("acc12hr"),
                "acc24h_mm":mm("acc24hr"),
                "acc48h_mm":mm("acc48hr"),
                "acc72h_mm":mm("acc72hr"),
                "acc96h_mm":mm("acc96hr"),
                "station_type":row.get("tipoestacao"),
            })

        if not stations:
            raise RuntimeError("Nenhum pluviômetro de Petrópolis encontrado")

        recent=[s for s in stations if s["status"]=="ok"]
        anomalies=[s for s in stations if s["status"]=="time_anomaly"]
        def highest(key):
            # Only fresh, internally time-consistent stations contribute to
            # dashboard maxima. Stale/anomalous values remain visible in the table.
            vals=[s for s in recent if isinstance(s.get(key),(int,float))]
            if not vals: return {"value":None,"station":None}
            top=max(vals,key=lambda s:s[key])
            return {"value":top[key],"station":top["name"]}

        order={"ok":0,"time_anomaly":1,"stale":2}
        stations.sort(key=lambda s:(order.get(s["status"],3),-(s.get("acc24h_mm") or 0),s["name"]))
        return {
            "provider":"CEMADEN",
            "status":"ok" if recent else "stale",
            "source_note":"Dados brutos do Mapa Interativo do CEMADEN; horários de origem em UTC convertidos para Brasília.",
            "collected_at":now.isoformat(),
            "url":CEMADEN_PLUVIO,
            "total_stations":len(stations),
            "recent_stations":len(recent),
            "time_anomaly_stations":len(anomalies),
            "highest_1h":highest("acc1h_mm"),
            "highest_24h":highest("acc24h_mm"),
            "stations":stations,
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"CEMADEN",
            "status":"unavailable",
            "collected_at":now.isoformat(),
            "url":CEMADEN_PLUVIO,
            "error":str(exc)[:600],
        })
        return fallback

def parse_portuguese_datetime(text):
    if not text:
        return None
    value=re.sub(r"\\s+"," ",str(text)).strip()
    parsed=parse_dt(value)
    if parsed:
        return parsed
    months={
        "JANEIRO":1,"FEVEREIRO":2,"MARCO":3,"ABRIL":4,"MAIO":5,"JUNHO":6,
        "JULHO":7,"AGOSTO":8,"SETEMBRO":9,"OUTUBRO":10,"NOVEMBRO":11,"DEZEMBRO":12,
    }
    clean=norm(value)
    m=re.search(r"(\\d{1,2})\\s+(JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)\\s+(\\d{4})(?:\\s+(\\d{1,2}):(\\d{2}))?",clean)
    if not m:
        return None
    try:
        return datetime(int(m.group(3)),months[m.group(2)],int(m.group(1)),int(m.group(4) or 0),int(m.group(5) or 0),tzinfo=TZ)
    except Exception:
        return None

def article_datetime(soup):
    for attrs in (
        {"property":"article:published_time"},
        {"itemprop":"datePublished"},
        {"name":"date"},
    ):
        tag=soup.find("meta",attrs=attrs)
        if tag and tag.get("content"):
            d=parse_portuguese_datetime(tag.get("content"))
            if d: return d
    text=soup.get_text(" ",strip=True)
    return parse_portuguese_datetime(text[:2500])

def fetch_defesa_civil(previous):
    prev=(previous.get("sources") or {}).get("defesa_civil",{})
    now=datetime.now(TZ)
    channels={
        "emergency":"199",
        "sms":"40199",
        "whatsapp":DEFESA_CIVIL_WHATSAPP,
        "bulletin":DEFESA_CIVIL_BOLETIM,
        "home":DEFESA_CIVIL_HOME,
    }
    try:
        home=requests.get(DEFESA_CIVIL_HOME,timeout=20,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
        home.raise_for_status()
        tag=requests.get(DEFESA_CIVIL_TAG,timeout=20,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
        tag.raise_for_status()
        soup=BeautifulSoup(tag.text,"html.parser")
        links=[]
        for a in soup.find_all("a",href=True):
            href=a.get("href","")
            if "/noticias/item/" not in href:
                continue
            if href.startswith("/"):
                href="https://www.petropolis.rj.gov.br"+href
            elif not href.startswith("http"):
                href="https://www.petropolis.rj.gov.br/pmp/"+href.lstrip("./")
            if href not in links:
                links.append(href)
            if len(links)>=15:
                break

        latest_stage=None
        latest_news=None
        for url in links:
            try:
                r=requests.get(url,timeout=12,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
                r.raise_for_status()
                art=BeautifulSoup(r.text,"html.parser")
                title_el=art.find("h1") or art.find("h2")
                title=re.sub(r"\\s+"," ",title_el.get_text(" ",strip=True) if title_el else "").strip()
                published=article_datetime(art)
                text_body=re.sub(r"\\s+"," ",art.get_text(" ",strip=True))
                item={"title":title or "Notícia da Defesa Civil","url":url,"published_at":published.isoformat() if published else None}
                if published and (latest_news is None or published>latest_news[0]):
                    latest_news=(published,item)
                body_norm=norm(text_body)
                m=re.search(r"ESTAGIO OPERACIONAL\\s*[:\\-]?\\s*(VIGILANCIA|OBSERVACAO|ATENCAO|ALERTA MAXIMO|ALERTA|CRISE)",body_norm)
                if m and published and (latest_stage is None or published>latest_stage[0]):
                    latest_stage=(published,m.group(1),item)
            except Exception:
                continue

        latest_news_item=latest_news[1] if latest_news else None
        if latest_stage:
            published,stage_norm,item=latest_stage
            age=round((now-published).total_seconds()/3600,1)
            if age<=48:
                stage_label={
                    "VIGILANCIA":"Vigilância","OBSERVACAO":"Observação","ATENCAO":"Atenção",
                    "ALERTA":"Alerta","ALERTA MAXIMO":"Alerta Máximo","CRISE":"Crise"
                }[stage_norm]
                return {
                    "name":"Defesa Civil",
                    "provider":"Defesa Civil de Petrópolis",
                    "status":"ok",
                    "stage":stage_label,
                    "risk":stage_label,
                    "level":DC_LEVEL[stage_norm],
                    "official_updated_at":published.isoformat(),
                    "age_hours":age,
                    "latest_bulletin":item,
                    "latest_news":latest_news_item,
                    "channels":channels,
                    "collected_at":now.isoformat(),
                    "url":DEFESA_CIVIL_HOME,
                    "error":None,
                }

        return {
            "name":"Defesa Civil",
            "provider":"Defesa Civil de Petrópolis",
            "status":"no_recent_bulletin",
            "stage":None,
            "risk":"Sem estágio recente automatizável",
            "level":None,
            "official_updated_at":latest_stage[0].isoformat() if latest_stage else None,
            "age_hours":round((now-latest_stage[0]).total_seconds()/3600,1) if latest_stage else None,
            "latest_bulletin":latest_stage[2] if latest_stage else None,
            "latest_news":latest_news_item,
            "channels":channels,
            "collected_at":now.isoformat(),
            "url":DEFESA_CIVIL_HOME,
            "error":None,
        }
    except Exception as exc:
        return {
            "name":"Defesa Civil",
            "provider":"Defesa Civil de Petrópolis",
            "status":"unavailable",
            "stage":None,
            "risk":"Fonte indisponível",
            "level":None,
            "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
            "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
            "channels":channels,
            "collected_at":now.isoformat(),
            "url":DEFESA_CIVIL_HOME,
            "error":str(exc)[:500],
        }

def fetch_roads(previous):
    prev=previous.get("roads") or {}
    now=datetime.now(TZ)
    try:
        r=requests.get(ELOVIAS_HOME,timeout=18,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
        r.raise_for_status()
        return {
            "provider":"Elovias",
            "scope":"BR-040/495 MG/RJ e Serra de Petrópolis",
            "status":"official_channel_available",
            "traffic_status":"Consulta oficial necessária",
            "automated_traffic":False,
            "emergency_phone":"0800-040-0495",
            "accessibility_phone":"0800-040-1495",
            "whatsapp":"(21) 98040-0113",
            "home_url":ELOVIAS_HOME,
            "map_url":ELOVIAS_MAP,
            "collected_at":now.isoformat(),
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"Elovias",
            "scope":"BR-040/495 MG/RJ e Serra de Petrópolis",
            "status":"unavailable",
            "traffic_status":"Não determinado",
            "automated_traffic":False,
            "home_url":ELOVIAS_HOME,
            "map_url":ELOVIAS_MAP,
            "collected_at":now.isoformat(),
            "error":str(exc)[:400],
        })
        return fallback

def history_snapshot(payload):
    sources=payload.get("sources") or {}
    pv=payload.get("pluviometers") or {}
    return {
        "ts":payload.get("generated_at"),
        "level":(payload.get("overall") or {}).get("level"),
        "label":(payload.get("overall") or {}).get("label"),
        "reason":(payload.get("overall") or {}).get("reason"),
        "geological":{
            "level":(sources.get("cemaden_geological") or {}).get("level"),
            "risk":(sources.get("cemaden_geological") or {}).get("risk"),
            "status":(sources.get("cemaden_geological") or {}).get("status"),
        },
        "hydrological":{
            "level":(sources.get("cemaden_hydrological") or {}).get("level"),
            "risk":(sources.get("cemaden_hydrological") or {}).get("risk"),
            "status":(sources.get("cemaden_hydrological") or {}).get("status"),
        },
        "inmet":{
            "level":(sources.get("inmet_alerts") or {}).get("level"),
            "risk":(sources.get("inmet_alerts") or {}).get("risk"),
            "status":(sources.get("inmet_alerts") or {}).get("status"),
        },
        "defesa_civil":{
            "level":(sources.get("defesa_civil") or {}).get("level"),
            "stage":(sources.get("defesa_civil") or {}).get("stage"),
            "status":(sources.get("defesa_civil") or {}).get("status"),
        },
        "pluviometers":{
            "highest_1h":pv.get("highest_1h"),
            "highest_24h":pv.get("highest_24h"),
            "recent_stations":pv.get("recent_stations"),
            "total_stations":pv.get("total_stations"),
        },
        "weather":{
            "status":(payload.get("weather") or {}).get("status"),
            "temperature_c":(payload.get("weather") or {}).get("temperature_c"),
        },
        "roads":{
            "status":(payload.get("roads") or {}).get("status"),
            "traffic_status":(payload.get("roads") or {}).get("traffic_status"),
        },
    }

def persist_history(payload):
    os.makedirs(ARCHIVE_DIR,exist_ok=True)
    now=parse_dt(payload.get("generated_at")) or datetime.now(TZ)
    snap=history_snapshot(payload)

    history=[]
    try:
        with open(HISTORY_OUT,"r",encoding="utf-8") as f:
            raw=json.load(f)
            history=raw.get("snapshots",[]) if isinstance(raw,dict) else raw
    except Exception:
        try:
            raw=get_json(HISTORY_URL,timeout=10)
            history=raw.get("snapshots",[]) if isinstance(raw,dict) else raw
        except Exception:
            history=[]

    history=[x for x in history if isinstance(x,dict) and x.get("ts")!=snap.get("ts")]
    history.append(snap)
    history.sort(key=lambda x:str(x.get("ts") or ""))
    cutoff=now-timedelta(days=90)
    rolling=[]
    for x in history:
        d=parse_dt(x.get("ts"))
        if d and d>=cutoff:
            rolling.append(x)
    with open(HISTORY_OUT,"w",encoding="utf-8") as f:
        json.dump({
            "schema_version":1,
            "generated_at":now.isoformat(),
            "retention_days":90,
            "snapshots":rolling,
        },f,ensure_ascii=False,separators=(",",":"))

    month=now.strftime("%Y-%m")
    archive_path=os.path.join(ARCHIVE_DIR,month+".json")
    archive=[]
    try:
        with open(archive_path,"r",encoding="utf-8") as f:
            raw=json.load(f)
            archive=raw.get("snapshots",[]) if isinstance(raw,dict) else raw
    except Exception:
        archive=[]
    archive=[x for x in archive if isinstance(x,dict) and x.get("ts")!=snap.get("ts")]
    archive.append(snap)
    archive.sort(key=lambda x:str(x.get("ts") or ""))
    with open(archive_path,"w",encoding="utf-8") as f:
        json.dump({"schema_version":1,"month":month,"snapshots":archive},f,ensure_ascii=False,separators=(",",":"))

    months=[]
    for name in sorted(os.listdir(ARCHIVE_DIR),reverse=True):
        if not re.fullmatch(r"\d{4}-\d{2}\.json",name):
            continue
        path=os.path.join(ARCHIVE_DIR,name)
        count=None
        try:
            with open(path,"r",encoding="utf-8") as f:
                raw=json.load(f)
                count=len(raw.get("snapshots",[]) if isinstance(raw,dict) else raw)
        except Exception:
            pass
        months.append({"month":name[:-5],"file":"data/archive/"+name,"snapshots":count})
    with open(HISTORY_INDEX_OUT,"w",encoding="utf-8") as f:
        json.dump({"schema_version":1,"generated_at":now.isoformat(),"months":months},f,ensure_ascii=False,separators=(",",":"))

def fetch_inmet_forecast(previous):
    prev=previous.get("forecast") or {}
    now=datetime.now(TZ)
    try:
        data=get_json(INMET_FORECAST, timeout=25)
        root=data
        if isinstance(data,dict) and "3303906" in data:
            root=data["3303906"]
        if not isinstance(root,dict):
            raise RuntimeError("Formato inesperado da previsão INMET")

        days=[]
        for date_key, day in sorted(root.items(), key=lambda kv: str(kv[0])):
            if not isinstance(day,dict):
                continue
            periods=[]
            for period_name in ("manha","tarde","noite"):
                p=day.get(period_name)
                if isinstance(p,dict):
                    periods.append((period_name,p))
            if not periods and any(k in day for k in ("resumo","tempo","temp_min","temp_max")):
                periods=[("dia",day)]
            if not periods:
                continue

            mins=[safe_float(p.get("temp_min")) for _,p in periods]
            maxs=[safe_float(p.get("temp_max")) for _,p in periods]
            hum_min=[safe_float(p.get("umidade_min")) for _,p in periods]
            hum_max=[safe_float(p.get("umidade_max")) for _,p in periods]
            mins=[v for v in mins if v is not None]
            maxs=[v for v in maxs if v is not None]
            hum_min=[v for v in hum_min if v is not None]
            hum_max=[v for v in hum_max if v is not None]

            summaries=[]
            period_payload=[]
            for name,p in periods:
                summary=str(p.get("resumo") or p.get("tempo") or "").strip()
                if summary and summary not in summaries:
                    summaries.append(summary)
                period_payload.append({
                    "period":name,
                    "summary":summary or None,
                    "temperature_min_c":safe_float(p.get("temp_min")),
                    "temperature_max_c":safe_float(p.get("temp_max")),
                    "humidity_min_pct":safe_float(p.get("umidade_min")),
                    "humidity_max_pct":safe_float(p.get("umidade_max")),
                    "wind_direction":p.get("dir_vento"),
                    "wind_intensity":p.get("int_vento"),
                    "weekday":p.get("dia_semana"),
                })

            days.append({
                "date":str(date_key),
                "summary":" / ".join(summaries) if summaries else "Previsão disponível",
                "temperature_min_c":min(mins) if mins else None,
                "temperature_max_c":max(maxs) if maxs else None,
                "humidity_min_pct":min(hum_min) if hum_min else None,
                "humidity_max_pct":max(hum_max) if hum_max else None,
                "periods":period_payload,
            })
            if len(days)>=3:
                break

        if not days:
            raise RuntimeError("Previsão INMET sem dias válidos")

        return {
            "provider":"INMET",
            "status":"ok",
            "municipality_code":"3303906",
            "municipality":"Petrópolis/RJ",
            "collected_at":now.isoformat(),
            "url":INMET_FORECAST,
            "days":days,
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"INMET",
            "status":"unavailable",
            "municipality_code":"3303906",
            "municipality":"Petrópolis/RJ",
            "collected_at":now.isoformat(),
            "url":INMET_FORECAST,
            "error":str(exc)[:600],
        })
        return fallback

def main():
    os.makedirs("data",exist_ok=True)
    previous=load_previous()
    geo=fetch_cemaden(1,"cemaden_geological","Deslizamento",previous)
    hydro=fetch_cemaden(2,"cemaden_hydrological","Hidrológico",previous)
    weather=fetch_inmet_weather(previous)
    pluviometers=fetch_cemaden_pluviometers(previous)
    forecast=fetch_inmet_forecast(previous)
    inmet=fetch_inmet_alerts(previous)
    defesa=fetch_defesa_civil(previous)
    roads=fetch_roads(previous)

    usable=[s for s in (geo,hydro,inmet,defesa) if isinstance(s.get("level"),int)]
    overall=max([s["level"] for s in usable], default=int((previous.get("overall") or {}).get("level") or 1))

    # Optional combined-source escalation: only independent providers count.
    cemaden_level=max([s.get("level") or 0 for s in (geo,hydro)])
    inmet_level=inmet.get("level") if isinstance(inmet.get("level"),int) else 0
    escalated=False
    if cemaden_level>=3 and inmet_level>=3:
        overall=min(5,max(cemaden_level,inmet_level)+1)
        escalated=True

    top=[]
    for s in (geo,hydro,inmet,defesa):
        if isinstance(s.get("level"),int) and s["level"]>=max(1,overall-(1 if escalated else 0)):
            top.append(f'{s["name"]}: {s.get("risk")}')
    reason=", ".join(top) or "Sem nova leitura válida."
    if escalated: reason += ". Escalada por duas fontes independentes em nível 3 ou superior."
    unavailable=[s["name"] for s in (geo,hydro,inmet,defesa) if s.get("status")=="unavailable"]
    stale=[s["name"] for s in (geo,hydro) if s.get("status")=="stale"]
    if unavailable: reason += ". Fonte(s) indisponível(is): "+", ".join(unavailable)+"; último valor válido preservado quando disponível"
    if stale: reason += ". Atenção: atualização oficial antiga em "+", ".join(stale)+"."

    now=datetime.now(TZ)
    payload={
      "schema_version":2,
      "generated_at":now.isoformat(),
      "location":{"city":"Petrópolis","state":"RJ","country":"Brasil"},
      "overall":{"level":overall,"label":LEVEL_LABELS[overall],"reason":reason,"rule":"Maior nível válido entre CEMADEN-RJ e INMET; se ambas as fontes independentes estiverem em nível >=3, escalada configurada de +1."},
      "sources":{"cemaden_geological":geo,"cemaden_hydrological":hydro,"inmet_alerts":inmet,"defesa_civil":defesa},
      "weather":weather,
      "pluviometers":pluviometers,
      "forecast":forecast,
      "roads":roads,
      "integrations":{"cemaden_rj":"active","inmet_alerts":"active","inmet_forecast":forecast.get("status","unavailable"),"inmet_weather":weather.get("status","unavailable"),"pluviometers":pluviometers.get("status","unavailable"),"defesa_civil":defesa.get("status","unavailable"),"roads":roads.get("status","unavailable"),"utilities":"pending"}
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    persist_history(payload)
    print(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
