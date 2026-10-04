#!/usr/bin/env python3
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
CITY = "PETRÓPOLIS"
OUT = os.path.join("data", "status.json")
HISTORY_OUT = os.path.join("data", "history.json")
HISTORY_INDEX_OUT = os.path.join("data", "history_index.json")
CLIMATE_HISTORY_OUT = os.path.join("data", "climate_history.json")
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
DEFESA_CIVIL_METEO_PAGE = "https://www.petropolis.rj.gov.br/pmp/index.php/boletim-meteorologico-defesa-civil"
DEFESA_CIVIL_JOURNALISM = "https://www.petropolis.rj.gov.br/pmp/index.php/noticias/itemlist/user/257-jornalismo"
DEFESA_CIVIL_RSS = DEFESA_CIVIL_JOURNALISM + "?format=feed&type=rss"
DEFESA_CIVIL_CACHE_READER = "https://r.jina.ai/"
DEFESA_CIVIL_NEWS_INDEX = "https://news.google.com/rss/search"
DEFESA_CIVIL_WHATSAPP = "https://whatsapp.com/channel/0029VaKX3R5D38CZuMcmc03i"
HST_LAT = -22.50825
HST_LON = -43.19345
OPEN_METEO_CURRENT = "https://api.open-meteo.com/v1/forecast"
RAINVIEWER_MAPS = "https://api.rainviewer.com/public/weather-maps.json"
ELOVIAS_API = "https://cliente.api.elovias.com.br/v1/"

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
        raw_risk=match[2].strip().upper()
        if raw_risk not in RISK_TO_LEVEL: raise RuntimeError(f"Risco não reconhecido: {raw_risk}")
        observed=parse_dt(match[3])
        age=round((now-observed).total_seconds()/3600,1) if observed else None

        if age is None or age>24:
            return {
                "name":label,
                "provider":"CEMADEN-RJ / Defesa Civil RJ",
                "status":"no_recent_update",
                "risk":"Sem atualização oficial recente",
                "level":None,
                "official_updated_at":observed.isoformat() if observed else match[3],
                "age_hours":age,
                "last_known_risk":raw_risk.title(),
                "last_known_level":RISK_TO_LEVEL[raw_risk],
                "last_known_updated_at":observed.isoformat() if observed else match[3],
                "freshness_limit_hours":24,
                "message":"Sem atualização oficial relevante nas últimas 24 h. Isso não significa ausência de risco.",
                "collected_at":now.isoformat(),
                "url":url,
                "error":None,
            }

        return {
            "name":label,
            "provider":"CEMADEN-RJ / Defesa Civil RJ",
            "status":"ok",
            "risk":raw_risk.title(),
            "level":RISK_TO_LEVEL[raw_risk],
            "official_updated_at":observed.isoformat() if observed else match[3],
            "age_hours":age,
            "last_known_risk":raw_risk.title(),
            "last_known_level":RISK_TO_LEVEL[raw_risk],
            "last_known_updated_at":observed.isoformat() if observed else match[3],
            "freshness_limit_hours":24,
            "message":"Informação oficial dentro da janela de 24 h.",
            "collected_at":now.isoformat(),
            "url":url,
            "error":None,
        }
    except Exception as exc:
        return {
            "name":label,
            "provider":"CEMADEN-RJ / Defesa Civil RJ",
            "status":"source_unconfirmed",
            "risk":"Sem informação oficial recente confirmada",
            "level":None,
            "last_known_risk":prev.get("last_known_risk") or prev.get("risk"),
            "last_known_level":prev.get("last_known_level") if prev.get("last_known_level") is not None else prev.get("level"),
            "last_known_updated_at":prev.get("last_known_updated_at") or prev.get("official_updated_at"),
            "freshness_limit_hours":24,
            "message":"Não foi possível confirmar uma atualização oficial recente nesta coleta. Isso não significa ausência de risco.",
            "collected_at":now.isoformat(),
            "url":url,
            "error":str(exc)[:300],
        }

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
            r=requests.get(url,timeout=5,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0","Accept":"application/json,text/plain,*/*"})
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

def weather_code_text(code):
    mapping={
        0:"Céu limpo",1:"Predominantemente limpo",2:"Parcialmente nublado",3:"Nublado",
        45:"Nevoeiro",48:"Nevoeiro com geada",
        51:"Garoa fraca",53:"Garoa moderada",55:"Garoa forte",
        61:"Chuva fraca",63:"Chuva moderada",65:"Chuva forte",
        80:"Pancadas de chuva fracas",81:"Pancadas de chuva moderadas",82:"Pancadas de chuva fortes",
        95:"Trovoada",96:"Trovoada com granizo fraco",99:"Trovoada com granizo forte",
    }
    return mapping.get(int(code) if code is not None else -1,"Condição não classificada")

def fetch_open_meteo_current(previous):
    prev=previous.get("weather_reference") or {}
    now=datetime.now(TZ)
    params={
        "latitude":HST_LAT,
        "longitude":HST_LON,
        "current":"temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,weather_code,wind_speed_10m,wind_gusts_10m",
        "timezone":"America/Sao_Paulo",
    }
    try:
        r=requests.get(OPEN_METEO_CURRENT,params=params,timeout=18,headers={"User-Agent":"HST-Alerta/1.0"})
        r.raise_for_status()
        data=r.json()
        cur=data.get("current") or {}
        observed=parse_dt(cur.get("time"))
        if observed is None and cur.get("time"):
            try:
                observed=datetime.fromisoformat(str(cur["time"])).replace(tzinfo=TZ)
            except Exception:
                observed=None
        age=round((now-observed).total_seconds()/3600,1) if observed else None
        status="ok" if age is not None and -0.25<=age<=2 else "stale"
        return {
            "provider":"Open-Meteo",
            "source_type":"estimativa meteorológica complementar",
            "official":False,
            "status":status,
            "location":{"name":"Hospital Santa Teresa","latitude":HST_LAT,"longitude":HST_LON},
            "observed_at":observed.isoformat() if observed else cur.get("time"),
            "age_hours":age,
            "temperature_c":safe_float(cur.get("temperature_2m")),
            "apparent_temperature_c":safe_float(cur.get("apparent_temperature")),
            "humidity_pct":safe_float(cur.get("relative_humidity_2m")),
            "precipitation_mm":safe_float(cur.get("precipitation")),
            "rain_mm":safe_float(cur.get("rain")),
            "wind_speed_kmh":safe_float(cur.get("wind_speed_10m")),
            "wind_gust_kmh":safe_float(cur.get("wind_gusts_10m")),
            "weather_code":cur.get("weather_code"),
            "condition":weather_code_text(cur.get("weather_code")),
            "collected_at":now.isoformat(),
            "url":r.url,
            "message":"Referência complementar próxima ao HST. Não substitui INMET, CEMADEN ou Defesa Civil para alertas oficiais.",
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"Open-Meteo",
            "source_type":"estimativa meteorológica complementar",
            "official":False,
            "status":"source_unconfirmed",
            "collected_at":now.isoformat(),
            "message":"Sem condição meteorológica complementar recente confirmada. Isso não significa ausência de risco.",
            "error":str(exc)[:500],
        })
        return fallback

def fetch_weather_map(previous):
    prev=previous.get("weather_map") or {}
    now=datetime.now(TZ)

    # Operational grid centered on HST, covering central Petrópolis.
    lat_offsets=(-0.09,-0.06,-0.03,0.0,0.03,0.06,0.09)
    lon_offsets=(-0.12,-0.08,-0.04,0.0,0.04,0.08,0.12)
    coords=[(round(HST_LAT+a,5),round(HST_LON+b,5)) for a in lat_offsets for b in lon_offsets]

    grid=[]
    grid_error=None
    try:
        params={
            "latitude":",".join(str(x[0]) for x in coords),
            "longitude":",".join(str(x[1]) for x in coords),
            "current":"temperature_2m,cloud_cover,precipitation,wind_speed_10m,wind_direction_10m,weather_code",
            "timezone":"America/Sao_Paulo",
        }
        r=requests.get(OPEN_METEO_CURRENT,params=params,timeout=25,headers={"User-Agent":"HST-Alerta/1.0"})
        r.raise_for_status()
        data=r.json()
        items=data if isinstance(data,list) else [data]
        for requested,item in zip(coords,items):
            if not isinstance(item,dict):
                continue
            cur=item.get("current") or {}
            grid.append({
                "latitude":safe_float(item.get("latitude")) if item.get("latitude") is not None else requested[0],
                "longitude":safe_float(item.get("longitude")) if item.get("longitude") is not None else requested[1],
                "requested_latitude":requested[0],
                "requested_longitude":requested[1],
                "observed_at":cur.get("time"),
                "temperature_c":safe_float(cur.get("temperature_2m")),
                "cloud_cover_pct":safe_float(cur.get("cloud_cover")),
                "precipitation_mm":safe_float(cur.get("precipitation")),
                "wind_speed_kmh":safe_float(cur.get("wind_speed_10m")),
                "wind_direction_deg":safe_float(cur.get("wind_direction_10m")),
                "weather_code":cur.get("weather_code"),
            })
    except Exception as exc:
        grid_error=str(exc)[:500]
        grid=(prev.get("grid") or []) if isinstance(prev,dict) else []

    radar={"status":"unavailable","provider":"RainViewer","error":None}
    try:
        r=requests.get(RAINVIEWER_MAPS,timeout=15,headers={"User-Agent":"HST-Alerta/1.0"})
        r.raise_for_status()
        data=r.json()
        frames=((data.get("radar") or {}).get("past") or [])
        if not frames:
            raise RuntimeError("RainViewer sem quadros de radar")
        frame=frames[-1]
        frame_time=datetime.fromtimestamp(int(frame.get("time")),tz=ZoneInfo("UTC")).astimezone(TZ)
        radar={
            "status":"ok",
            "provider":"RainViewer",
            "host":data.get("host"),
            "path":frame.get("path"),
            "frame_time":frame_time.isoformat(),
            "generated_unix":data.get("generated"),
            "tile_color_scheme":2,
            "url":RAINVIEWER_MAPS,
            "error":None,
        }
    except Exception as exc:
        prior=(prev.get("radar") or {}) if isinstance(prev,dict) else {}
        radar={
            "status":"unavailable",
            "provider":"RainViewer",
            "last_known_frame_time":prior.get("frame_time") or prior.get("last_known_frame_time"),
            "url":RAINVIEWER_MAPS,
            "error":str(exc)[:500],
        }

    grid_ok=bool(grid)
    return {
        "status":"ok" if grid_ok else "source_unconfirmed",
        "provider":"Open-Meteo + RainViewer",
        "source_type":"mapa meteorológico complementar",
        "official":False,
        "center":{"name":"Hospital Santa Teresa","latitude":HST_LAT,"longitude":HST_LON},
        "operational_radius_km":5,
        "grid_updated_at":now.isoformat() if grid_ok else prev.get("grid_updated_at"),
        "grid":grid,
        "grid_points":len(grid),
        "grid_error":grid_error,
        "radar":radar,
        "collected_at":now.isoformat(),
        "message":"Mapa complementar. Alertas e níveis HST permanecem baseados nas fontes oficiais integradas.",
    }

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

def distance_km(lat1,lon1,lat2,lon2):
    from math import radians,sin,cos,asin,sqrt
    r=6371.0
    p1,p2=radians(lat1),radians(lat2)
    dlat=radians(lat2-lat1)
    dlon=radians(lon2-lon1)
    a=sin(dlat/2)**2+cos(p1)*cos(p2)*sin(dlon/2)**2
    return 2*r*asin(sqrt(a))

# Coordenadas publicadas para estações CEMADEN usadas como referências
# geográficas do HST. Bingen - Geo é a estação verificada mais próxima
# dentre as estações CEMADEN com coordenadas consolidadas nesta base.
KNOWN_CEMADEN_COORDS={
    # Coordenadas cadastrais/estáticas das estações CEMADEN em Petrópolis.
    # Usadas somente para distância e ordenação; as chuvas continuam vindo
    # diretamente do endpoint de monitoramento do CEMADEN.
    "Bingen - Geo":(-22.51221,-43.20900),
    "Rua Araruama/Quitandinha":(-22.52000,-43.22000),
    "São Sebastião - Geo":(-22.53690,-43.19300),
    "Dr. Thouzet - Geo":(-22.52832,-43.20200),
    "Quitandinha - Geo":(-22.52490,-43.22300),
    "Rua Amazonas/Quitandinha":(-22.52900,-43.22300),
    "Morin":(-22.52700,-43.16100),
    "Mosela":(-22.48100,-43.21900),
    "Independência2":(-22.54800,-43.20900),
    "CIEP Brizolão137":(-22.45400,-43.14300),
    "Araras":(-22.42700,-43.24900),
    "Nogueira":(-22.41800,-43.12200),
    "Itaipava":(-22.38800,-43.13200),
    "Vila Constância":(-22.40100,-43.09700),
    "Itaipava2":(-22.36900,-43.11200),
    "Estrada da Cachoeira":(-22.35300,-43.09500),
    "Pedro do Rio":(-22.33500,-43.13400),
    "Vale do Cuiabá2":(-22.33600,-43.04700),
    "Vila Rica":(-22.34900,-43.13200),
    "Alto da Serra":(-22.53000,-43.17100),
    # As duas estações LNCC ficam no complexo do LNCC; referência do campus
    # usada para ordenação aproximada até haver coordenada individual publicada.
    "LNCC - Geo":(-22.52992,-43.21716),
    "LNCC - Geo 2":(-22.52992,-43.21716),
}

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
            station_name=row.get("nomeestacao") or "Estação sem nome"
            fallback_coords=KNOWN_CEMADEN_COORDS.get(station_name)
            row_lat=safe_float(row.get("latitude"))
            row_lon=safe_float(row.get("longitude"))
            station_lat=row_lat if row_lat is not None else (fallback_coords[0] if fallback_coords else None)
            station_lon=row_lon if row_lon is not None else (fallback_coords[1] if fallback_coords else None)
            stations.append({
                "id":row.get("idestacao"),
                "name":station_name,
                "status":health,
                "latitude":station_lat,
                "longitude":station_lon,
                "distance_to_hst_km":round(distance_km(HST_LAT,HST_LON,station_lat,station_lon),2) if station_lat is not None and station_lon is not None else None,
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
        georeferenced=[s for s in stations if isinstance(s.get("distance_to_hst_km"),(int,float))]
        georeferenced_recent=[s for s in recent if isinstance(s.get("distance_to_hst_km"),(int,float))]
        nearest=min(georeferenced_recent or georeferenced,key=lambda s:s["distance_to_hst_km"]) if georeferenced else None
        def highest(key):
            # Only fresh, internally time-consistent stations contribute to
            # dashboard maxima. Stale/anomalous values remain visible in the table.
            vals=[s for s in recent if isinstance(s.get(key),(int,float))]
            if not vals: return {"value":None,"station":None}
            top=max(vals,key=lambda s:s[key])
            return {"value":top[key],"station":top["name"]}

        stations.sort(
            key=lambda s:(
                s.get("distance_to_hst_km") is None,
                s.get("distance_to_hst_km") if s.get("distance_to_hst_km") is not None else 9999,
                s["name"],
            )
        )
        return {
            "provider":"CEMADEN",
            "status":"ok" if recent else "stale",
            "source_note":"Chuvas do Mapa Interativo do CEMADEN; horários em UTC convertidos para Brasília. Coordenadas estáticas são usadas somente para ordenar as estações por distância do HST.",
            "collected_at":now.isoformat(),
            "url":CEMADEN_PLUVIO,
            "total_stations":len(stations),
            "recent_stations":len(recent),
            "hidden_stations":len(stations)-len(recent),
            "time_anomaly_stations":len(anomalies),
            "georeferenced_stations":len(georeferenced),
            "coordinate_source":"cadastro estático consolidado das estações CEMADEN",
            "highest_1h":highest("acc1h_mm"),
            "highest_24h":highest("acc24h_mm"),
            "nearest_to_hst":nearest,
            "nearest_note":"Distância aproximada em linha reta entre o HST e estações CEMADEN com coordenadas verificadas.",
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
    stage_freshness_hours=48
    signal_freshness_hours=12
    channels={
        "emergency":"199",
        "sms":"40199",
        "whatsapp":DEFESA_CIVIL_WHATSAPP,
        "bulletin":DEFESA_CIVIL_BOLETIM,
        "home":DEFESA_CIVIL_HOME,
        "journalism":DEFESA_CIVIL_JOURNALISM,
        "rss":DEFESA_CIVIL_RSS,
    }
    headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"}

    def dc_get(url, timeout=4):
        candidates=[url]
        if "://www.petropolis.rj.gov.br/" in url:
            candidates.append(url.replace("://www.petropolis.rj.gov.br/","://petropolis.rj.gov.br/",1))
        last_error=None
        for candidate_url in candidates:
            try:
                response=requests.get(
                    candidate_url,
                    timeout=timeout,
                    headers={**headers,"Connection":"close"},
                    allow_redirects=True,
                )
                response.raise_for_status()
                if not response.text.strip():
                    raise RuntimeError("resposta vazia")
                return response
            except Exception as exc:
                last_error=exc
        raise last_error or RuntimeError("rota oficial indisponível")

    route_defs={
        "portal":DEFESA_CIVIL_HOME,
        "tag_defesa_civil":DEFESA_CIVIL_TAG,
        "jornalismo":DEFESA_CIVIL_JOURNALISM,
        "rss_jornalismo":DEFESA_CIVIL_RSS,
        "boletim_meteorologico":DEFESA_CIVIL_METEO_PAGE,
    }
    route_results={}
    route_pages={}

    # Independent routes are queried in parallel so one timeout does not block
    # the remaining official municipal channels.
    with ThreadPoolExecutor(max_workers=len(route_defs)) as executor:
        futures={
            executor.submit(dc_get,url,4):(key,url)
            for key,url in route_defs.items()
        }
        for future in as_completed(futures):
            key,url=futures[future]
            try:
                response=future.result()
                route_pages[key]=response.text
                route_results[key]={
                    "status":"ok",
                    "url":response.url,
                    "http_status":response.status_code,
                }
            except Exception as exc:
                route_results[key]={
                    "status":"unavailable",
                    "url":url,
                    "error":exc.__class__.__name__,
                }

    successful_routes=[key for key,item in route_results.items() if item.get("status")=="ok"]

    cache_result={
        "provider":"Jina Reader",
        "status":"not_used",
        "used":False,
        "can_escalate":False,
        "routes":{},
        "message":"Cache secundário não necessário nesta coleta.",
    }
    cache_pages={}
    cache_index_items=[]

    def cache_get_official(url, timeout=8):
        # Third-party reader used only as an intermediary cache of official URLs.
        # Its content is informational and is never allowed to create HST escalation.
        proxy_url=DEFESA_CIVIL_CACHE_READER+url
        response=requests.get(
            proxy_url,
            timeout=timeout,
            headers={"User-Agent":"HST-Alerta/1.0","Accept":"text/plain,*/*"},
        )
        response.raise_for_status()
        if not response.text.strip():
            raise RuntimeError("cache vazio")
        return response

    if not successful_routes:
        cache_targets={
            "jornalismo":DEFESA_CIVIL_JOURNALISM,
            "tag_defesa_civil":DEFESA_CIVIL_TAG,
            "boletim_meteorologico":DEFESA_CIVIL_METEO_PAGE,
        }
        cache_routes={}
        with ThreadPoolExecutor(max_workers=len(cache_targets)) as executor:
            futures={
                executor.submit(cache_get_official,url,8):(key,url)
                for key,url in cache_targets.items()
            }
            for future in as_completed(futures):
                key,url=futures[future]
                try:
                    response=future.result()
                    cache_pages[key]=response.text
                    cache_routes[key]={
                        "status":"ok",
                        "official_url":url,
                        "cache_url":response.url,
                    }
                except Exception as exc:
                    cache_routes[key]={
                        "status":"unavailable",
                        "official_url":url,
                        "error":exc.__class__.__name__,
                    }

        cache_ok=[key for key,item in cache_routes.items() if item.get("status")=="ok"]
        cache_result={
            "provider":"Jina Reader",
            "status":"ok" if cache_ok else "unavailable",
            "used":bool(cache_ok),
            "can_escalate":False,
            "routes":cache_routes,
            "available_routes":cache_ok,
            "message":(
                "Cache secundário ativo somente para contexto; não pode elevar o Nível HST."
                if cache_ok
                else "Proxy de leitura indisponível; tentando índice RSS secundário."
            ),
        }

        if not cache_ok:
            try:
                index_response=requests.get(
                    DEFESA_CIVIL_NEWS_INDEX,
                    params={
                        "q":'site:petropolis.rj.gov.br "Defesa Civil" Petrópolis',
                        "hl":"pt-BR",
                        "gl":"BR",
                        "ceid":"BR:pt-419",
                    },
                    timeout=8,
                    headers={"User-Agent":"HST-Alerta/1.0","Accept":"application/rss+xml,application/xml,text/xml,*/*"},
                )
                index_response.raise_for_status()
                root=ET.fromstring(index_response.text)
                for item in root.findall(".//item")[:20]:
                    title=(item.findtext("title") or "").strip()
                    link=(item.findtext("link") or "").strip()
                    pub_raw=(item.findtext("pubDate") or "").strip()
                    source=item.find("source")
                    source_name=(source.text or "").strip() if source is not None and source.text else None
                    source_url=source.attrib.get("url") if source is not None else None
                    published=None
                    if pub_raw:
                        try:
                            published=parsedate_to_datetime(pub_raw)
                            published=published.astimezone(TZ) if published.tzinfo else published.replace(tzinfo=TZ)
                        except Exception:
                            published=parse_portuguese_datetime(pub_raw)
                    combined=norm((title or "")+" "+(source_name or ""))
                    if "DEFESA CIVIL" not in combined and not any(
                        term in combined
                        for term in ("SIREN","PONTO DE APOIO","RISCO DE DESLIZAMENTO","CELL BROADCAST")
                    ):
                        continue
                    cache_index_items.append({
                        "title":title or "Publicação indexada da Defesa Civil",
                        "url":link or source_url or DEFESA_CIVIL_JOURNALISM,
                        "official_source_url":source_url,
                        "published":published,
                        "text":title,
                        "normalized":combined,
                        "verification":"cache_index",
                    })

                if cache_index_items:
                    cache_result={
                        "provider":"Google News RSS",
                        "status":"ok",
                        "used":True,
                        "can_escalate":False,
                        "routes":{
                            **cache_routes,
                            "news_index":{
                                "status":"ok",
                                "url":index_response.url,
                                "items":len(cache_index_items),
                            },
                        },
                        "available_routes":["news_index"],
                        "message":"Índice RSS secundário ativo somente para contexto; não pode elevar o Nível HST.",
                    }
                else:
                    cache_result["routes"]["news_index"]={
                        "status":"ok",
                        "url":index_response.url,
                        "items":0,
                    }
                    cache_result["message"]="Índice RSS respondeu, mas sem publicação relevante identificada nesta coleta."
            except Exception as exc:
                cache_result["routes"]["news_index"]={
                    "status":"unavailable",
                    "url":DEFESA_CIVIL_NEWS_INDEX,
                    "error":exc.__class__.__name__,
                }
                cache_result["message"]="Caches intermediários indisponíveis nesta coleta."

    def canonical_article_url(href):
        href=str(href or "").strip()
        if not href:
            return None
        if href.startswith("//"):
            href="https:"+href
        elif href.startswith("/"):
            href="https://www.petropolis.rj.gov.br"+href
        elif not href.startswith("http"):
            href="https://www.petropolis.rj.gov.br/pmp/"+href.lstrip("./")
        if "/noticias/item/" not in href:
            return None
        return href

    links=[]
    feed_items=[]

    # HTML routes provide article URLs even when the Defesa Civil landing page
    # itself is unavailable.
    for key,page in route_pages.items():
        soup=BeautifulSoup(page,"html.parser")
        for a_tag in soup.find_all("a",href=True):
            href=canonical_article_url(a_tag.get("href"))
            if href and href not in links:
                links.append(href)

        if key=="rss_jornalismo":
            for item_tag in soup.find_all("item"):
                title_tag=item_tag.find("title")
                link_tag=item_tag.find("link")
                date_tag=item_tag.find("pubdate")
                desc_tag=item_tag.find("description")
                title=re.sub(r"\s+"," ",title_tag.get_text(" ",strip=True) if title_tag else "").strip()
                url=canonical_article_url(link_tag.get_text(" ",strip=True) if link_tag else "")
                published=None
                if date_tag:
                    try:
                        published=parsedate_to_datetime(date_tag.get_text(" ",strip=True)).astimezone(TZ)
                    except Exception:
                        published=parse_portuguese_datetime(date_tag.get_text(" ",strip=True))
                description=re.sub(r"<[^>]+>"," ",desc_tag.decode_contents() if desc_tag else "")
                description=re.sub(r"\s+"," ",description).strip()
                if title or description:
                    feed_items.append({
                        "title":title or "Notícia da Defesa Civil",
                        "url":url or DEFESA_CIVIL_JOURNALISM,
                        "published":published,
                        "text":title+" "+description,
                        "route":"rss_jornalismo",
                    })

    # Keep the list bounded; broad Journalism pages can contain many unrelated links.
    links=links[:18]
    article_items=[]

    def fetch_article(url):
        response=dc_get(url,4)
        art=BeautifulSoup(response.text,"html.parser")
        title_el=art.find("h1") or art.find("h2")
        title=re.sub(r"\s+"," ",title_el.get_text(" ",strip=True) if title_el else "").strip()
        published=article_datetime(art)
        text_body=re.sub(r"\s+"," ",art.get_text(" ",strip=True))
        return {
            "title":title or "Notícia da Defesa Civil",
            "url":response.url or url,
            "published":published,
            "text":text_body,
            "route":"article",
        }

    if links:
        with ThreadPoolExecutor(max_workers=min(6,len(links))) as executor:
            futures={executor.submit(fetch_article,url):url for url in links}
            for future in as_completed(futures):
                try:
                    article_items.append(future.result())
                except Exception:
                    continue

    cached_context_items=[]

    def cached_datetime(text):
        text=str(text or "")
        for pattern in (
            r"(?im)^Published Time:\s*(.+)$",
            r"(?im)^Published:\s*(.+)$",
            r"(?im)^Data(?: de publicação)?\s*[:\-]\s*(.+)$",
        ):
            match=re.search(pattern,text)
            if match:
                raw=match.group(1).strip()
                parsed=parse_portuguese_datetime(raw)
                if parsed:
                    return parsed
                try:
                    parsed=parsedate_to_datetime(raw)
                    return parsed.astimezone(TZ) if parsed.tzinfo else parsed.replace(tzinfo=TZ)
                except Exception:
                    pass
        iso_match=re.search(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+\-]\d{2}:?\d{2})?",text)
        return parse_dt(iso_match.group(0)) if iso_match else None

    def cached_title(text, fallback):
        for pattern in (r"(?im)^Title:\s*(.+)$",r"(?m)^#\s+(.+)$"):
            match=re.search(pattern,str(text or ""))
            if match:
                return re.sub(r"\s+"," ",match.group(1)).strip()[:300]
        return fallback

    if cache_result.get("used"):
        cached_context_items.extend(cache_index_items)
        cached_links=[]
        for page in cache_pages.values():
            for match in re.findall(
                r"https?://(?:www\.)?petropolis\.rj\.gov\.br/[^\s\]\)\>\"']*?/noticias/item/[^\s\]\)\>\"']+",
                page,
                flags=re.I,
            ):
                clean=match.rstrip(".,;:")
                if clean not in cached_links:
                    cached_links.append(clean)

        cached_article_pages=[]
        for url in cached_links[:8]:
            try:
                response=cache_get_official(url,7)
                cached_article_pages.append((url,response.text))
            except Exception:
                continue

        source_pages=cached_article_pages or [
            (route_defs.get(key) or DEFESA_CIVIL_HOME,page)
            for key,page in cache_pages.items()
        ]
        for official_url,page in source_pages:
            normalized=norm(page)
            if not any(term in normalized for term in (
                "DEFESA CIVIL","SIREN","PONTO DE APOIO","PONTOS DE APOIO",
                "RISCO DE DESLIZAMENTO","RISCO DE INUNDACAO","ESTAGIO OPERACIONAL",
                "BOLETIM METEOROLOGICO","CELL BROADCAST",
            )):
                continue
            cached_context_items.append({
                "title":cached_title(page,"Publicação oficial recuperada via cache"),
                "url":official_url,
                "published":cached_datetime(page),
                "text":page[:18000],
                "normalized":normalized,
                "verification":"cache_indirect",
            })

        cached_context_items.sort(
            key=lambda item:item.get("published") or datetime.min.replace(tzinfo=TZ),
            reverse=True,
        )

    parsed_items=[]
    seen=set()
    dc_terms=(
        "DEFESA CIVIL","SIREN","PONTO DE APOIO","PONTOS DE APOIO",
        "RISCO DE DESLIZAMENTO","RISCO DE INUNDACAO","ESTAGIO OPERACIONAL",
        "BOLETIM METEOROLOGICO","CELL BROADCAST","CHUVAS","CHUVA FORTE",
    )
    for item in feed_items+article_items:
        combined=norm((item.get("title") or "")+" "+(item.get("text") or ""))
        if not any(term in combined for term in dc_terms):
            continue
        key=(item.get("url"),item.get("title"),item.get("published").isoformat() if item.get("published") else None)
        if key in seen:
            continue
        seen.add(key)
        item["normalized"]=combined
        parsed_items.append(item)

    latest_stage=None
    latest_signal=None
    latest_news=None
    latest_bulletin=None

    stage_labels={
        "VIGILANCIA":"Vigilância",
        "OBSERVACAO":"Observação",
        "ATENCAO":"Atenção",
        "ALERTA":"Alerta",
        "ALERTA MAXIMO":"Alerta Máximo",
        "CRISE":"Crise",
    }

    for entry in parsed_items:
        published=entry.get("published")
        if not published:
            continue
        item={
            "title":entry.get("title") or "Notícia da Defesa Civil",
            "url":entry.get("url"),
            "published_at":published.isoformat(),
        }
        combined=entry.get("normalized") or ""

        if latest_news is None or published>latest_news[0]:
            latest_news=(published,item)

        if "BOLETIM METEOROLOGICO" in combined and (latest_bulletin is None or published>latest_bulletin[0]):
            latest_bulletin=(published,item)

        stage_match=re.search(
            r"ESTAGIO OPERACIONAL\s*[:\-]?\s*(VIGILANCIA|OBSERVACAO|ATENCAO|ALERTA MAXIMO|ALERTA|CRISE)",
            combined,
        )
        if stage_match and (latest_stage is None or published>latest_stage[0]):
            latest_stage=(published,stage_match.group(1),item)

        signal=None
        if (
            ("DESMOBILIZA" in combined and "PONTO" in combined and "APOIO" in combined)
            or ("SIREN" in combined and any(x in combined for x in ("DESACION", "DESMOBIL", "NORMALIZ")))
        ):
            signal=(1,"stand_down","Desmobilização operacional informada")
        elif "SEGUNDO TOQUE" in combined and "SIREN" in combined:
            signal=(4,"second_siren","Segundo toque de sirene acionado")
        elif "CELL BROADCAST" in combined and "SEVER" in combined:
            signal=(4,"cell_broadcast_severe","Alerta severo via Cell Broadcast")
        elif (
            ("RISCO DE DESLIZAMENTO" in combined or "RISCO DE INUNDACAO" in combined)
            and ("SMS" in combined or "PONTOS DE APOIO" in combined or "CELL BROADCAST" in combined)
        ):
            signal=(4,"risk_alert","Alerta oficial de risco com mobilização")
        elif "PRIMEIRO TOQUE" in combined and "SIREN" in combined:
            signal=(3,"first_siren","Primeiro toque de sirene acionado")
        elif (
            "PONTOS DE APOIO" in combined
            and any(x in combined for x in ("ABRE ", "ABERTURA", "FORAM ABERTOS", "ESTAO ABERTOS"))
        ):
            signal=(3,"support_points","Pontos de apoio abertos")

        if signal and (latest_signal is None or published>latest_signal[0]):
            latest_signal=(published,signal[0],signal[1],signal[2],item)

    latest_news_item=latest_news[1] if latest_news else None
    latest_bulletin_item=latest_bulletin[1] if latest_bulletin else (latest_stage[2] if latest_stage else None)

    cache_latest=None
    cache_hint=None
    if cached_context_items:
        first=cached_context_items[0]
        cache_latest={
            "title":first.get("title"),
            "url":first.get("url"),
            "published_at":first.get("published").isoformat() if first.get("published") else None,
            "verification":first.get("verification") or "cache_indirect",
        }
        for cached in cached_context_items:
            combined=cached.get("normalized") or ""
            published=cached.get("published")
            if not published:
                continue
            age_hours=round((now-published).total_seconds()/3600,1)
            if age_hours < -0.25:
                continue

            hint=None
            freshness_limit=signal_freshness_hours
            if "SEGUNDO TOQUE" in combined and "SIREN" in combined:
                hint="Possível segundo toque de sirene localizado no cache"
            elif "PRIMEIRO TOQUE" in combined and "SIREN" in combined:
                hint="Possível primeiro toque de sirene localizado no cache"
            elif ("CELL BROADCAST" in combined or "CELLBROADCAST" in combined) and "SEVER" in combined:
                hint="Possível alerta severo localizado no cache"
            elif "PONTOS DE APOIO" in combined and any(x in combined for x in ("ABRE ", "ABERTURA", "ESTAO ABERTOS")):
                hint="Possível abertura de pontos de apoio localizada no cache"
            elif "ESTAGIO OPERACIONAL" in combined:
                hint="Possível atualização de estágio operacional localizada no cache"
                freshness_limit=stage_freshness_hours

            if hint and age_hours<=freshness_limit:
                cache_hint={
                    "label":hint,
                    "url":cached.get("url"),
                    "published_at":published.isoformat(),
                    "age_hours":age_hours,
                    "can_escalate":False,
                }
                break

    stage_candidate=None
    if latest_stage:
        stage_published,stage_norm,stage_item=latest_stage
        stage_age=round((now-stage_published).total_seconds()/3600,1)
        if -0.25<=stage_age<=stage_freshness_hours:
            stage_candidate={
                "published":stage_published,
                "level":DC_LEVEL[stage_norm],
                "label":stage_labels[stage_norm],
                "item":stage_item,
                "age_hours":stage_age,
            }

    signal_candidate=None
    if latest_signal:
        signal_published,signal_level,signal_type,signal_label,signal_item=latest_signal
        signal_age=round((now-signal_published).total_seconds()/3600,1)
        if -0.25<=signal_age<=signal_freshness_hours:
            signal_candidate={
                "published":signal_published,
                "level":signal_level,
                "type":signal_type,
                "label":signal_label,
                "item":signal_item,
                "age_hours":signal_age,
            }

    effective=None
    basis=None

    # A newer explicit municipal stage supersedes an older operational signal.
    # Otherwise both recent official signals are considered conservatively.
    if stage_candidate and signal_candidate and stage_candidate["published"]>=signal_candidate["published"]:
        effective=stage_candidate["level"]
        basis="Estágio operacional: "+stage_candidate["label"]
    else:
        candidates=[x for x in (stage_candidate,signal_candidate) if x]
        if candidates:
            effective=max(x["level"] for x in candidates)
            strongest=max(candidates,key=lambda x:x["level"])
            if strongest is stage_candidate:
                basis="Estágio operacional: "+stage_candidate["label"]
            else:
                basis=signal_candidate["label"]

    common={
        "name":"Defesa Civil",
        "provider":"Defesa Civil de Petrópolis",
        "channels":channels,
        "routes":route_results,
        "route_summary":{
            "available":len(successful_routes),
            "total":len(route_defs),
            "available_routes":successful_routes,
        },
        "cache":cache_result,
        "cache_hint":cache_hint,
        "freshness":{
            "stage_hours":stage_freshness_hours,
            "operational_signal_hours":signal_freshness_hours,
        },
        "collected_at":now.isoformat(),
        "url":DEFESA_CIVIL_HOME,
    }

    if effective is not None:
        newest_times=[x["published"] for x in (stage_candidate,signal_candidate) if x]
        official_updated=max(newest_times) if newest_times else None
        stage_label=stage_candidate["label"] if stage_candidate else None
        risk_label=LEVEL_LABELS.get(effective,"Informação operacional")
        return {
            **common,
            "status":"ok",
            "stage":stage_label,
            "risk":risk_label,
            "level":effective,
            "basis":basis,
            "official_updated_at":official_updated.isoformat() if official_updated else None,
            "age_hours":round((now-official_updated).total_seconds()/3600,1) if official_updated else None,
            "latest_bulletin":latest_bulletin_item,
            "latest_news":latest_news_item,
            "operational_signal":{
                "type":signal_candidate["type"],
                "label":signal_candidate["label"],
                "level":signal_candidate["level"],
                "published_at":signal_candidate["published"].isoformat(),
                "url":signal_candidate["item"].get("url"),
            } if signal_candidate else None,
            "message":"Informação operacional oficial recente da Defesa Civil de Petrópolis.",
            "error":None,
        }

    if successful_routes:
        return {
            **common,
            "status":"no_recent_update",
            "stage":None,
            "risk":"Sem atualização oficial recente",
            "level":None,
            "basis":None,
            "official_updated_at":latest_stage[0].isoformat() if latest_stage else None,
            "age_hours":round((now-latest_stage[0]).total_seconds()/3600,1) if latest_stage else None,
            "latest_bulletin":latest_bulletin_item,
            "latest_news":latest_news_item,
            "operational_signal":None,
            "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
            "last_known_level":prev.get("level") if prev.get("level") is not None else prev.get("last_known_level"),
            "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
            "message":f"{len(successful_routes)} de {len(route_defs)} rotas oficiais responderam; sem estágio ou sinal operacional recente dentro da janela definida.",
            "error":None,
        }

    if cache_result.get("used"):
        return {
            **common,
            "status":"cache_only",
            "stage":None,
            "risk":"Fonte direta indisponível · cache secundário ativo",
            "level":None,
            "basis":None,
            "official_updated_at":None,
            "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
            "last_known_level":prev.get("level") if prev.get("level") is not None else prev.get("last_known_level"),
            "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
            "latest_bulletin":prev.get("latest_bulletin"),
            "latest_news":cache_latest or prev.get("latest_news"),
            "operational_signal":None,
            "message":"Rotas oficiais diretas indisponíveis; cache secundário recuperou contexto. Conteúdo indireto não pode elevar o Nível HST.",
            "error":"Todas as rotas oficiais diretas ficaram indisponíveis nesta coleta.",
        }

    return {
        **common,
        "status":"source_unconfirmed",
        "stage":None,
        "risk":"Sem informação oficial recente confirmada",
        "level":None,
        "basis":None,
        "official_updated_at":None,
        "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
        "last_known_level":prev.get("level") if prev.get("level") is not None else prev.get("last_known_level"),
        "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
        "latest_bulletin":prev.get("latest_bulletin"),
        "latest_news":prev.get("latest_news"),
        "operational_signal":None,
        "message":"Nenhuma das rotas oficiais diretas nem o cache secundário puderam ser confirmados nesta coleta. Isso não significa ausência de risco.",
        "error":"Rotas oficiais e cache secundário indisponíveis nesta coleta.",
    }

def _elovias_get(endpoint, timeout=15):
    r=requests.get(
        ELOVIAS_API+endpoint,
        timeout=timeout,
        headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0","Accept":"application/json"},
    )
    r.raise_for_status()
    return r.json()

def _iso_sort_value(item):
    return str((item or {}).get("data") or (item or {}).get("dataTempo") or "")

def _plain_excerpt(text, limit=460):
    text=re.sub(r"<[^>]+>"," ",str(text or ""))
    text=re.sub(r"\s+"," ",text).strip()
    if len(text)<=limit:
        return text
    return text[:limit].rsplit(" ",1)[0]+"…"

def _serra_excerpt(item):
    text=str((item or {}).get("texto") or (item or {}).get("chamada") or "")
    clean=re.sub(r"\s+"," ",text).strip()
    m=re.search(
        r"(Na\s+Serra\s+de\s+Petrópolis[,\s:;-]+.*?)(?=(?:\s+No\s+trecho|\s+Na\s+Baixada|\s+Em\s+Minas|\s+A\s+Elovias|$))",
        clean,
        flags=re.I,
    )
    return _plain_excerpt(m.group(1) if m else clean,520)

def fetch_roads(previous):
    prev=previous.get("roads") or {}
    now=datetime.now(TZ)
    endpoint_status={}
    alerts=[]
    weather_points=[]
    recent_news=[]
    bulletins=[]

    for key,endpoint in (
        ("alerts","aviso"),
        ("weather","mapa/trecho-principal"),
        ("news","noticia/recente"),
        ("bulletins","boletim"),
    ):
        try:
            data=_elovias_get(endpoint)
            endpoint_status[key]="ok"
            if key=="alerts":
                alerts=data if isinstance(data,list) else []
            elif key=="weather":
                weather_points=data if isinstance(data,list) else []
            elif key=="news":
                recent_news=data if isinstance(data,list) else []
            elif key=="bulletins":
                bulletins=data if isinstance(data,list) else []
        except Exception as exc:
            endpoint_status[key]="unavailable"
            prior=prev.get(key)
            if key=="alerts" and isinstance(prior,list): alerts=prior
            if key=="weather" and isinstance(prior,list): weather_points=prior
            if key=="news" and isinstance(prior,list): recent_news=prior
            if key=="bulletins" and isinstance(prior,list): bulletins=prior

    petropolis=None
    for item in weather_points:
        if norm(item.get("nome"))=="PETROPOLIS":
            petropolis={
                "name":"Petrópolis",
                "condition":item.get("condicaoTempoDescription"),
                "weather_icon":item.get("condicaoTempoIcon"),
                "temperature_c":safe_float(item.get("temperatura")),
                "wind_speed_kmh":safe_float(item.get("vento")),
                "wind_direction_deg":safe_float(item.get("ventoDirecao")),
                "wind_direction":item.get("ventoDirecaoDescription"),
                "updated_at":item.get("dataTempo"),
            }
            break

    recent_news=sorted(
        [x for x in recent_news if isinstance(x,dict)],
        key=_iso_sort_value,
        reverse=True,
    )
    bulletins=sorted(
        [x for x in bulletins if isinstance(x,dict)],
        key=_iso_sort_value,
        reverse=True,
    )

    latest_news=recent_news[0] if recent_news else None
    latest_serra=None
    latest_schedule=None
    for item in recent_news:
        hay=norm(" ".join([
            str(item.get("titulo") or ""),
            str(item.get("chamada") or ""),
            str(item.get("texto") or ""),
        ]))
        if latest_serra is None and "SERRA DE PETROPOLIS" in hay:
            latest_serra=item
        if latest_schedule is None and "CRONOGRAMA DE OBRAS" in norm(item.get("titulo")):
            latest_schedule=item
        if latest_serra is not None and latest_schedule is not None:
            break

    def news_payload(item):
        if not item:
            return None
        slug=item.get("slug")
        return {
            "title":item.get("titulo"),
            "summary":_plain_excerpt(item.get("chamada") or item.get("texto"),520),
            "serra_summary":_serra_excerpt(item) if "SERRA DE PETROPOLIS" in norm(str(item.get("texto") or "")+" "+str(item.get("chamada") or "")) else None,
            "published_at":item.get("data"),
            "slug":slug,
            "url":("https://elovias.com.br/noticias/"+slug) if slug else None,
            "image_url":item.get("imagemUrl"),
        }

    latest_bulletin=None
    if bulletins:
        item=bulletins[0]
        latest_bulletin={
            "title":item.get("titulo"),
            "published_at":item.get("data"),
            "pdf_url":item.get("arquivoUrl"),
            "image_url":item.get("imagemUrl"),
            "pages":len(item.get("paginas") or []),
        }

    active_alerts=[]
    for a in alerts[:10]:
        if not isinstance(a,dict):
            continue
        active_alerts.append({
            "title":a.get("titulo") or a.get("nome") or a.get("assunto") or "Aviso Elovias",
            "message":_plain_excerpt(a.get("texto") or a.get("mensagem") or a.get("descricao") or a.get("chamada"),500),
            "start_at":a.get("dataInicio") or a.get("inicio") or a.get("data"),
            "end_at":a.get("dataFim") or a.get("fim"),
            "raw_type":a.get("tipo"),
        })

    connected=sum(1 for v in endpoint_status.values() if v=="ok")
    return {
        "provider":"Elovias",
        "scope":"BR-040/495 MG/RJ · Serra de Petrópolis",
        "status":"ok" if connected>=2 else ("degraded" if connected else "unavailable"),
        "api_base":ELOVIAS_API,
        "endpoint_status":endpoint_status,
        "active_alerts":active_alerts,
        "active_alert_count":len(active_alerts),
        "no_active_alerts":endpoint_status.get("alerts")=="ok" and not active_alerts,
        "petropolis_weather":petropolis,
        "latest_news":news_payload(latest_news),
        "latest_serra_update":news_payload(latest_serra),
        "latest_schedule":news_payload(latest_schedule),
        "latest_bulletin":latest_bulletin,
        "alerts":alerts,
        "weather":weather_points,
        "news":recent_news[:8],
        "bulletins":bulletins[:4],
        "emergency_phone":"0800-040-0495",
        "accessibility_phone":"0800-040-1495",
        "whatsapp":"(21) 98040-0113",
        "home_url":"https://elovias.com.br/home",
        "map_url":"https://elovias.com.br/mapa",
        "collected_at":now.isoformat(),
        "message":"Dados consultados diretamente na API pública usada pelo portal oficial da Elovias. Ausência de aviso ativo não equivale a garantia de tráfego livre.",
        "error":None if connected else "Nenhum endpoint da API Elovias respondeu nesta coleta.",
    }

def history_snapshot(payload):
    sources=payload.get("sources") or {}
    pv=payload.get("pluviometers") or {}
    bingen=next(
        (s for s in (pv.get("stations") or []) if str((s or {}).get("name") or "").strip().casefold()=="bingen - geo"),
        None,
    )
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
            "risk":(sources.get("defesa_civil") or {}).get("risk"),
            "status":(sources.get("defesa_civil") or {}).get("status"),
            "stage":(sources.get("defesa_civil") or {}).get("stage"),
            "basis":(sources.get("defesa_civil") or {}).get("basis"),
            "official_updated_at":(sources.get("defesa_civil") or {}).get("official_updated_at"),
            "signal_type":((sources.get("defesa_civil") or {}).get("operational_signal") or {}).get("type"),
            "signal_label":((sources.get("defesa_civil") or {}).get("operational_signal") or {}).get("label"),
        },
        "pluviometers":{
            "highest_1h":pv.get("highest_1h"),
            "highest_24h":pv.get("highest_24h"),
            "recent_stations":pv.get("recent_stations"),
            "total_stations":pv.get("total_stations"),
            "bingen":{
                "name":bingen.get("name"),
                "status":bingen.get("status"),
                "observed_at":bingen.get("observed_at"),
                "age_hours":bingen.get("age_hours"),
                "distance_to_hst_km":bingen.get("distance_to_hst_km"),
                "acc1h_mm":bingen.get("acc1h_mm"),
                "acc3h_mm":bingen.get("acc3h_mm"),
                "acc6h_mm":bingen.get("acc6h_mm"),
                "acc12h_mm":bingen.get("acc12h_mm"),
                "acc24h_mm":bingen.get("acc24h_mm"),
                "acc48h_mm":bingen.get("acc48h_mm"),
                "acc72h_mm":bingen.get("acc72h_mm"),
                "acc96h_mm":bingen.get("acc96h_mm"),
            } if bingen else None,
        },
        "weather":{
            "status":(payload.get("weather_reference") or {}).get("status") or (payload.get("weather") or {}).get("status"),
            "provider":(payload.get("weather_reference") or {}).get("provider") or (payload.get("weather") or {}).get("provider"),
            "temperature_c":(payload.get("weather_reference") or {}).get("temperature_c") if (payload.get("weather_reference") or {}).get("temperature_c") is not None else (payload.get("weather") or {}).get("temperature_c"),
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

def climate_period(dt):
    hour=dt.hour
    if 0 <= hour < 6:
        return "madrugada", "Madrugada"
    if 6 <= hour < 12:
        return "manha", "Manhã"
    if 12 <= hour < 18:
        return "tarde", "Tarde"
    return "noite", "Noite"

def climate_sample(weather, weather_reference, now):
    official_ok=(weather or {}).get("status")=="ok" and (weather or {}).get("temperature_c") is not None
    source=weather if official_ok else weather_reference
    if not isinstance(source,dict) or source.get("status")!="ok":
        return None
    temp=safe_float(source.get("temperature_c"))
    if temp is None:
        return None
    return {
        "observed_at":source.get("observed_at") or now.isoformat(),
        "provider":source.get("provider"),
        "source_type":"oficial" if official_ok else "complementar",
        "condition":source.get("condition") or ("Observação meteorológica" if official_ok else "Condição atual"),
        "weather_code":source.get("weather_code"),
        "temperature_c":temp,
        "apparent_temperature_c":safe_float(source.get("apparent_temperature_c")),
        "humidity_pct":safe_float(source.get("humidity_pct")),
        "precipitation_mm":safe_float(source.get("precipitation_mm") if source.get("precipitation_mm") is not None else source.get("rain_1h_mm")),
        "wind_speed_kmh":safe_float(source.get("wind_speed_kmh")),
        "wind_gust_kmh":safe_float(source.get("wind_gust_kmh")),
    }

def persist_climate_history(weather, weather_reference):
    now=datetime.now(TZ)
    period_key,period_label=climate_period(now)
    if not period_key:
        return

    sample=climate_sample(weather,weather_reference,now)
    if not sample:
        return

    try:
        with open(CLIMATE_HISTORY_OUT,"r",encoding="utf-8") as f:
            raw=json.load(f)
            periods=raw.get("periods",[]) if isinstance(raw,dict) else raw
    except Exception:
        periods=[]

    date_key=now.strftime("%Y-%m-%d")
    existing=None
    for item in periods:
        if isinstance(item,dict) and item.get("date")==date_key and item.get("period")==period_key:
            existing=item
            break

    if existing is None:
        existing={
            "date":date_key,
            "period":period_key,
            "period_label":period_label,
            "first_observed_at":sample["observed_at"],
            "last_observed_at":sample["observed_at"],
            "samples":0,
            "provider":sample.get("provider"),
            "source_type":sample.get("source_type"),
            "condition":sample.get("condition"),
            "weather_code":sample.get("weather_code"),
            "temperature_min_c":sample.get("temperature_c"),
            "temperature_max_c":sample.get("temperature_c"),
            "temperature_last_c":sample.get("temperature_c"),
            "apparent_temperature_c":sample.get("apparent_temperature_c"),
            "humidity_min_pct":sample.get("humidity_pct"),
            "humidity_max_pct":sample.get("humidity_pct"),
            "humidity_last_pct":sample.get("humidity_pct"),
            "precipitation_last_mm":sample.get("precipitation_mm"),
            "wind_speed_last_kmh":sample.get("wind_speed_kmh"),
            "wind_gust_max_kmh":sample.get("wind_gust_kmh"),
        }
        periods.append(existing)

    existing["samples"]=int(existing.get("samples") or 0)+1
    existing["last_observed_at"]=sample["observed_at"]
    existing["provider"]=sample.get("provider")
    existing["source_type"]=sample.get("source_type")
    existing["condition"]=sample.get("condition")
    existing["weather_code"]=sample.get("weather_code")
    existing["temperature_last_c"]=sample.get("temperature_c")
    existing["apparent_temperature_c"]=sample.get("apparent_temperature_c")
    existing["humidity_last_pct"]=sample.get("humidity_pct")
    existing["precipitation_last_mm"]=sample.get("precipitation_mm")
    existing["wind_speed_last_kmh"]=sample.get("wind_speed_kmh")

    for key,value,mode in (
        ("temperature_min_c",sample.get("temperature_c"),"min"),
        ("temperature_max_c",sample.get("temperature_c"),"max"),
        ("humidity_min_pct",sample.get("humidity_pct"),"min"),
        ("humidity_max_pct",sample.get("humidity_pct"),"max"),
        ("wind_gust_max_kmh",sample.get("wind_gust_kmh"),"max"),
    ):
        if value is None:
            continue
        old=existing.get(key)
        if old is None:
            existing[key]=value
        elif mode=="min":
            existing[key]=min(old,value)
        else:
            existing[key]=max(old,value)

    cutoff=(now-timedelta(days=90)).date()
    kept=[]
    for item in periods:
        try:
            d=datetime.strptime(item.get("date",""),"%Y-%m-%d").date()
            if d>=cutoff:
                kept.append(item)
        except Exception:
            continue

    order={"madrugada":0,"manha":1,"tarde":2,"noite":3}
    kept.sort(key=lambda x:(x.get("date",""),order.get(x.get("period"),9)))
    with open(CLIMATE_HISTORY_OUT,"w",encoding="utf-8") as f:
        json.dump({
            "schema_version":1,
            "generated_at":now.isoformat(),
            "retention_days":90,
            "period_definition":{
                "madrugada":"00:00–05:59",
                "manha":"06:00–11:59",
                "tarde":"12:00–17:59",
                "noite":"18:00–23:59"
            },
            "periods":kept,
        },f,ensure_ascii=False,separators=(",",":"))

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

OBS_RAIN_1H_MM=20.0
OBS_RAIN_24H_MM=50.0
BINGEN_SAFE_MAX_AGE_HOURS=2.0
BINGEN_SAFE_SHORT_MM=20.0
BINGEN_SAFE_24H_MM=50.0
DEESCALATION_OFFICIAL_HOLD_HOURS=2
DEESCALATION_CONFIRMATIONS=3
DEESCALATION_CONFIRMATION_MIN_INTERVAL_MINUTES=10

def supplemental_observation_signals(forecast, pluviometers):
    """Complementary signals may raise only the HST Observation level (2).

    They are intentionally not allowed to create Attention/Alert/Crisis by
    themselves. Higher levels remain driven by official risk/alert sources.
    """
    signals=[]

    if isinstance(forecast,dict) and forecast.get("status")=="ok":
        heavy_terms=(
            "CHUVA FORTE","CHUVAS FORTES","CHUVA INTENSA","CHUVAS INTENSAS",
            "TEMPESTADE","FORTES PANCADAS","CHUVA VOLUMOSA","ACUMULADO DE CHUVA",
        )
        for day in (forecast.get("days") or [])[:2]:
            summary=str((day or {}).get("summary") or "")
            normalized=norm(summary)
            if any(term in normalized for term in heavy_terms):
                label=str((day or {}).get("date") or "próximas horas")
                signals.append(f"Previsão INMET com chuva forte/intensa ou tempestade ({label}).")
                break

    if isinstance(pluviometers,dict) and pluviometers.get("status")=="ok":
        h1=pluviometers.get("highest_1h") or {}
        h24=pluviometers.get("highest_24h") or {}
        v1=safe_float(h1.get("value"))
        v24=safe_float(h24.get("value"))
        if v1 is not None and v1>=OBS_RAIN_1H_MM:
            station=h1.get("station") or "estação CEMADEN"
            signals.append(f"Chuva elevada no CEMADEN: {v1:.1f} mm em 1 h em {station}.")
        if v24 is not None and v24>=OBS_RAIN_24H_MM:
            station=h24.get("station") or "estação CEMADEN"
            signals.append(f"Acumulado elevado no CEMADEN: {v24:.1f} mm em 24 h em {station}.")

    return signals

def hydrological_normalization_evidence(hydro, geo, inmet, defesa, forecast, pluviometers):
    """Evaluate whether stale hydrological information can stop blocking de-escalation.

    Bingen - Geo is a local corroborating sensor, not a replacement for the
    official CEMADEN hydrological risk classification.
    """
    evidence={
        "applicable":False,
        "safe":False,
        "reference_station":"Bingen - Geo",
        "reference_distance_km":None,
        "bingen_status":None,
        "bingen_age_hours":None,
        "bingen_short_window":"1 h",
        "bingen_short_mm":None,
        "bingen_24h_mm":None,
        "city_highest_1h_mm":None,
        "city_highest_24h_mm":None,
        "inmet_clear":False,
        "forecast_and_pluvio_clear":False,
        "defesa_clear":False,
        "geological_old_allowed":False,
        "checks":{},
        "blockers":[],
    }

    hydro_status=(hydro or {}).get("status")
    hydro_age=safe_float((hydro or {}).get("age_hours"))
    evidence["applicable"]=bool(
        hydro_status=="no_recent_update"
        and hydro_age is not None
        and hydro_age>=(hydro or {}).get("freshness_limit_hours",24)
    )
    if not evidence["applicable"]:
        evidence["blockers"].append("Hidrológico ainda não está em condição de dado antigo elegível.")
        return evidence

    stations=(pluviometers or {}).get("stations") or []
    bingen=next(
        (s for s in stations if norm((s or {}).get("name"))=="BINGEN - GEO"),
        None,
    )
    if not bingen:
        evidence["blockers"].append("Bingen - Geo não localizado na coleta.")
        return evidence

    evidence["reference_distance_km"]=bingen.get("distance_to_hst_km")
    evidence["bingen_status"]=bingen.get("status")
    evidence["bingen_age_hours"]=safe_float(bingen.get("age_hours"))

    bingen_1h=safe_float(bingen.get("acc1h_mm"))
    bingen_12h=safe_float(bingen.get("acc12h_mm"))
    bingen_24h=safe_float(bingen.get("acc24h_mm"))
    # If the 1 h accumulator is temporarily absent, a longer non-negative
    # accumulation below the same 20 mm threshold is a conservative upper bound
    # for any contained 1 h interval. Prefer 12 h, then 24 h.
    if bingen_1h is not None:
        short_mm=bingen_1h
        short_window="1 h"
    elif bingen_12h is not None:
        short_mm=bingen_12h
        short_window="12 h (substituto conservador)"
    else:
        short_mm=bingen_24h
        short_window="24 h (substituto conservador)"
    evidence["bingen_short_window"]=short_window
    evidence["bingen_short_mm"]=short_mm
    evidence["bingen_24h_mm"]=bingen_24h

    city_h1=safe_float(((pluviometers or {}).get("highest_1h") or {}).get("value"))
    city_h24=safe_float(((pluviometers or {}).get("highest_24h") or {}).get("value"))
    evidence["city_highest_1h_mm"]=city_h1
    evidence["city_highest_24h_mm"]=city_h24

    bingen_recent=(
        bingen.get("status")=="ok"
        and evidence["bingen_age_hours"] is not None
        and -0.25<=evidence["bingen_age_hours"]<=BINGEN_SAFE_MAX_AGE_HOURS
    )
    bingen_short_safe=short_mm is not None and short_mm<BINGEN_SAFE_SHORT_MM
    bingen_24h_safe=(
        evidence["bingen_24h_mm"] is not None
        and evidence["bingen_24h_mm"]<BINGEN_SAFE_24H_MM
    )
    city_1h_safe=city_h1 is not None and city_h1<OBS_RAIN_1H_MM
    city_24h_safe=city_h24 is not None and city_h24<OBS_RAIN_24H_MM

    inmet_clear=(
        (inmet or {}).get("status")=="ok"
        and isinstance((inmet or {}).get("level"),int)
        and int(inmet.get("level"))<=1
    )
    evidence["inmet_clear"]=inmet_clear

    supplemental=supplemental_observation_signals(forecast,pluviometers)
    evidence["forecast_and_pluvio_clear"]=not supplemental

    defesa_clear=not (
        (defesa or {}).get("status")=="ok"
        and isinstance((defesa or {}).get("level"),int)
        and int(defesa.get("level"))>=2
    )
    evidence["defesa_clear"]=defesa_clear

    geo_status=(geo or {}).get("status")
    evidence["geological_old_allowed"]=geo_status=="no_recent_update"

    checks={
        "bingen_recent":bingen_recent,
        "bingen_short_safe":bingen_short_safe,
        "bingen_24h_safe":bingen_24h_safe,
        "city_1h_safe":city_1h_safe,
        "city_24h_safe":city_24h_safe,
        "inmet_clear":inmet_clear,
        "forecast_and_pluvio_clear":evidence["forecast_and_pluvio_clear"],
        "defesa_clear":defesa_clear,
    }
    evidence["checks"]=checks

    labels={
        "bingen_recent":"Bingen - Geo sem leitura recente",
        "bingen_short_safe":"Bingen - Geo acima do limite de curto prazo",
        "bingen_24h_safe":"Bingen - Geo acima do limite de 24 h",
        "city_1h_safe":"Há pluviômetro recente em Petrópolis acima do limite de 1 h",
        "city_24h_safe":"Há pluviômetro recente em Petrópolis acima do limite de 24 h",
        "inmet_clear":"INMET não confirma cenário de normalização",
        "forecast_and_pluvio_clear":"Previsão ou pluviometria ainda contém gatilho de Observação",
        "defesa_clear":"Defesa Civil possui sinal operacional oficial recente",
    }
    evidence["blockers"]=[labels[k] for k,v in checks.items() if not v]
    evidence["safe"]=all(checks.values())
    return evidence


def deescalation_source_readiness(geo, hydro, inmet, local_hydro):
    """Return blockers for de-escalation, treating stale data differently from outage."""
    blockers=[]

    inmet_status=(inmet or {}).get("status")
    if not (
        inmet_status=="ok"
        and isinstance((inmet or {}).get("level"),int)
    ):
        blockers.append("INMET sem confirmação atual")

    hydro_status=(hydro or {}).get("status")
    if hydro_status=="ok" and isinstance((hydro or {}).get("level"),int):
        pass
    elif hydro_status=="no_recent_update":
        if not (local_hydro or {}).get("safe"):
            blockers.append("Hidrológico antigo sem confirmação local segura pelo Bingen")
    else:
        blockers.append("Hidrológico indisponível ou não confirmado")

    geo_status=(geo or {}).get("status")
    if geo_status=="ok" and isinstance((geo or {}).get("level"),int):
        pass
    elif geo_status=="no_recent_update":
        # A geological classification older than its validity window no longer
        # freezes de-escalation. If it updates again, it immediately participates.
        pass
    else:
        blockers.append("Geológico indisponível ou não confirmado")

    return blockers


def _official_event_from_source(key, source, allow_last_known=False):
    """Return a stable official elevated-risk event without treating rereads as new."""
    if not isinstance(source,dict):
        return None

    status=source.get("status")
    level=source.get("level")
    historical=False

    if not (status=="ok" and isinstance(level,int) and level>1):
        if not allow_last_known:
            return None
        level=source.get("last_known_level")
        if not (isinstance(level,int) and level>1):
            return None
        historical=True

    if key in ("cemaden_geological","cemaden_hydrological"):
        risk=(source.get("last_known_risk") if historical else source.get("risk")) or ""
        official_at=(
            source.get("last_known_updated_at")
            if historical
            else source.get("official_updated_at")
        ) or source.get("official_updated_at")
        identity={
            "source":key,
            "level":level,
            "risk":risk,
            "official_at":official_at,
        }
    elif key=="inmet_alerts":
        official_at=source.get("starts_at") or source.get("official_updated_at")
        identity={
            "source":key,
            "level":level,
            "title":source.get("title"),
            "starts_at":source.get("starts_at"),
            "ends_at":source.get("ends_at"),
        }
    elif key=="defesa_civil":
        signal=source.get("operational_signal") or {}
        bulletin=source.get("latest_bulletin") or {}
        official_at=(
            source.get("official_updated_at")
            or signal.get("published_at")
            or bulletin.get("published_at")
        )
        identity={
            "source":key,
            "level":level,
            "stage":source.get("stage"),
            "basis":source.get("basis"),
            "signal_type":source.get("signal_type"),
            "signal_label":source.get("signal_label"),
            "official_at":official_at,
        }
    else:
        return None

    signature=json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return {
        "source":key,
        "level":int(level),
        "signature":signature,
        "official_at":official_at,
        "historical":historical,
    }


def _official_driver_floor(level):
    """Minimum official source level capable of sustaining the current HST level."""
    try:
        level=int(level)
    except Exception:
        return 2
    if level<=2:
        return 2
    if level==3:
        return 3
    return level-1


def update_official_hold_state(previous, current_level, geo, hydro, inmet, defesa):
    """Track new official publications and derive the two-hour no-descent window."""
    now=datetime.now(TZ)
    prev_overall=(previous.get("overall") or {}) if isinstance(previous,dict) else {}
    prev_state=prev_overall.get("deescalation") or {}
    tracked=dict(prev_state.get("official_signals") or {})
    prev_sources=(previous.get("sources") or {}) if isinstance(previous,dict) else {}

    sources={
        "cemaden_geological":geo,
        "cemaden_hydrological":hydro,
        "inmet_alerts":inmet,
        "defesa_civil":defesa,
    }

    for key,source in sources.items():
        event=_official_event_from_source(key,source,allow_last_known=True)

        # If the current source no longer carries an elevated event, migrate the
        # last known official event from the previous payload when needed.
        if event is None and key not in tracked:
            event=_official_event_from_source(
                key,
                prev_sources.get(key),
                allow_last_known=True,
            )

        if event is None:
            continue

        old=tracked.get(key) or {}
        same_signature=old.get("signature")==event.get("signature")

        parsed_official=parse_dt(event.get("official_at"))
        if parsed_official and parsed_official>now+timedelta(minutes=15):
            parsed_official=None

        if same_signature:
            effective_at=(
                parse_dt(old.get("effective_at"))
                or parsed_official
                or parse_dt(old.get("first_seen_at"))
                or now
            )
            first_seen=parse_dt(old.get("first_seen_at")) or effective_at
        else:
            # Historical/stale CEMADEN information must retain its original
            # timestamp and must not become "new" merely because the code saw it.
            effective_at=parsed_official or now
            first_seen=now

        tracked[key]={
            "source":key,
            "level":event.get("level"),
            "signature":event.get("signature"),
            "official_at":(
                parsed_official.isoformat()
                if parsed_official
                else event.get("official_at")
            ),
            "effective_at":effective_at.isoformat(),
            "first_seen_at":first_seen.isoformat(),
            "last_seen_at":now.isoformat(),
            "historical":bool(event.get("historical")),
        }

    floor=_official_driver_floor(current_level)
    relevant=[]
    for item in tracked.values():
        try:
            lvl=int(item.get("level") or 0)
        except Exception:
            lvl=0
        when=parse_dt(item.get("effective_at"))
        if lvl>=floor and when:
            relevant.append((when,item))

    if not relevant:
        return {
            "official_signals":tracked,
            "official_driver_floor":floor,
            "last_relevant_official_at":None,
            "last_relevant_official_source":None,
            "official_hold_until":None,
            "official_hold_active":False,
            "official_hold_remaining_minutes":0,
        }

    last_when,last_item=max(relevant,key=lambda x:x[0])
    hold_until=last_when+timedelta(hours=DEESCALATION_OFFICIAL_HOLD_HOURS)
    remaining=max(0.0,(hold_until-now).total_seconds()/60.0)

    return {
        "official_signals":tracked,
        "official_driver_floor":floor,
        "last_relevant_official_at":last_when.isoformat(),
        "last_relevant_official_source":last_item.get("source"),
        "official_hold_until":hold_until.isoformat(),
        "official_hold_active":remaining>0,
        "official_hold_remaining_minutes":round(remaining,1),
    }


def apply_deescalation_hysteresis(candidate_level, previous, geo, hydro, inmet, defesa, local_hydro):
    """Escalate immediately; descend after 2 h quiet + 3 spaced safe checks."""
    now=datetime.now(TZ)
    prev_overall=(previous.get("overall") or {}) if isinstance(previous,dict) else {}
    try:
        previous_level=int(prev_overall.get("level") or 1)
    except Exception:
        previous_level=1

    prior_state=prev_overall.get("deescalation") or {}
    blockers=deescalation_source_readiness(geo,hydro,inmet,local_hydro)
    mode="bingen_local" if (local_hydro or {}).get("safe") else "standard"
    official=update_official_hold_state(
        previous,previous_level,geo,hydro,inmet,defesa
    )

    def state_base():
        return {
            "mode":mode,
            "local_hydrological_evidence":local_hydro,
            "official_hold_hours":DEESCALATION_OFFICIAL_HOLD_HOURS,
            "confirmations_required":DEESCALATION_CONFIRMATIONS,
            "confirmation_min_interval_minutes":DEESCALATION_CONFIRMATION_MIN_INTERVAL_MINUTES,
            **official,
        }

    if candidate_level>=previous_level:
        return candidate_level,{
            **state_base(),
            "pending":False,
            "phase":"stable_or_escalating",
            "target_level":None,
            "blocked_by_source_gap":False,
            "blocked_by_official_hold":False,
            "blocked_reasons":blockers,
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
        },False

    target=max(candidate_level,previous_level-1)

    # A new/recent official publication supporting the current elevated level
    # blocks any descent for two full hours. Re-reading the same publication does
    # not restart this timer because its stable signature is preserved above.
    if official.get("official_hold_active"):
        return previous_level,{
            **state_base(),
            "pending":True,
            "phase":"official_hold",
            "target_level":target,
            "blocked_by_source_gap":False,
            "blocked_by_official_hold":True,
            "blocked_reasons":[],
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
        },True

    if blockers:
        return previous_level,{
            **state_base(),
            "pending":True,
            "phase":"blocked",
            "target_level":target,
            "blocked_by_source_gap":True,
            "blocked_by_official_hold":False,
            "blocked_reasons":blockers,
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
        },True

    same_target=(
        prior_state.get("phase")=="confirming"
        and prior_state.get("target_level")==target
        and not prior_state.get("blocked_by_source_gap")
        and not prior_state.get("blocked_by_official_hold")
    )
    count=int(prior_state.get("consecutive_confirmations") or 0) if same_target else 0
    last_confirmation=parse_dt(prior_state.get("last_confirmation_at")) if same_target else None

    may_count=(
        last_confirmation is None
        or (now-last_confirmation).total_seconds()/60.0
           >=DEESCALATION_CONFIRMATION_MIN_INTERVAL_MINUTES
    )
    if may_count:
        count+=1
        last_confirmation=now

    if count>=DEESCALATION_CONFIRMATIONS:
        new_level=target
        still_pending=candidate_level<new_level
        next_target=max(candidate_level,new_level-1) if still_pending else None
        return new_level,{
            **state_base(),
            "pending":still_pending,
            "phase":"confirming" if still_pending else "stable",
            "target_level":next_target,
            "blocked_by_source_gap":False,
            "blocked_by_official_hold":False,
            "blocked_reasons":[],
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
            "last_step_at":now.isoformat(),
        },False

    return previous_level,{
        **state_base(),
        "pending":True,
        "phase":"confirming",
        "target_level":target,
        "blocked_by_source_gap":False,
        "blocked_by_official_hold":False,
        "blocked_reasons":[],
        "consecutive_confirmations":count,
        "last_confirmation_at":last_confirmation.isoformat() if last_confirmation else None,
    },True

def main():
    os.makedirs("data",exist_ok=True)
    previous=load_previous()
    geo=fetch_cemaden(1,"cemaden_geological","Deslizamento",previous)
    hydro=fetch_cemaden(2,"cemaden_hydrological","Hidrológico",previous)
    weather=fetch_inmet_weather(previous)
    weather_reference=fetch_open_meteo_current(previous)
    weather_map=fetch_weather_map(previous)
    pluviometers=fetch_cemaden_pluviometers(previous)
    forecast=fetch_inmet_forecast(previous)
    inmet=fetch_inmet_alerts(previous)
    defesa=fetch_defesa_civil(previous)
    roads=fetch_roads(previous)

    # Only fresh/confirmed official sources may create a new escalation.
    # Stale or unavailable sources can hold a previous level through hysteresis,
    # but their preserved numeric level must not drive a new increase.
    usable=[
        s for s in (geo,hydro,inmet,defesa)
        if s.get("status")=="ok" and isinstance(s.get("level"),int)
    ]
    official_candidate=max([s["level"] for s in usable], default=1)

    # Corroboration between independent official providers can escalate one level
    # only when both providers involved have a current confirmed status.
    cemaden_level=max(
        [
            s.get("level") or 0
            for s in (geo,hydro)
            if s.get("status")=="ok" and isinstance(s.get("level"),int)
        ],
        default=0,
    )
    inmet_level=(
        inmet.get("level")
        if inmet.get("status")=="ok" and isinstance(inmet.get("level"),int)
        else 0
    )
    escalated=False
    candidate=official_candidate
    if cemaden_level>=3 and inmet_level>=3:
        candidate=max(candidate,min(5,max(cemaden_level,inmet_level)+1))
        escalated=True

    # Forecast and pluviometers are early-warning evidence only.
    supplemental_signals=supplemental_observation_signals(forecast,pluviometers)
    supplemental_observation=bool(supplemental_signals)
    if supplemental_observation and candidate<2:
        candidate=2

    local_hydro_normalization=hydrological_normalization_evidence(
        hydro,geo,inmet,defesa,forecast,pluviometers
    )

    overall,deescalation,deescalation_held=apply_deescalation_hysteresis(
        candidate,previous,geo,hydro,inmet,defesa,local_hydro_normalization
    )

    driver_floor=max(1,candidate-(1 if escalated else 0))
    top=[]
    for s in (geo,hydro,inmet,defesa):
        if (
            s.get("status")=="ok"
            and isinstance(s.get("level"),int)
            and s["level"]>=driver_floor
            and s["level"]>1
        ):
            detail=s.get("basis") or s.get("risk")
            top.append(f'{s["name"]}: {detail}')

    reason_parts=[]
    if top:
        reason_parts.append(", ".join(top))
    elif candidate<=1:
        reason_parts.append("Sem condição oficial de risco acima de Vigilância nas fontes integradas")

    if escalated:
        reason_parts.append("Escalada por corroboração de CEMADEN-RJ e INMET em nível 3 ou superior")
    if supplemental_signals:
        reason_parts.extend(supplemental_signals)

    gaps=[s["name"] for s in (geo,hydro,inmet) if s.get("status") in ("no_recent_update","source_unconfirmed","unavailable")]
    if gaps:
        reason_parts.append("Sem confirmação oficial recente em: "+", ".join(gaps))

    if local_hydro_normalization.get("safe"):
        short_value=local_hydro_normalization.get("bingen_short_mm")
        day_value=local_hydro_normalization.get("bingen_24h_mm")
        short_window=local_hydro_normalization.get("bingen_short_window") or "curto prazo"
        reason_parts.append(
            "Normalização hidrológica local elegível pelo Bingen - Geo "
            +f"({short_value:.1f} mm/{short_window}; {day_value:.1f} mm/24 h)"
        )

    if deescalation_held:
        if deescalation.get("blocked_by_official_hold"):
            remaining=safe_float(deescalation.get("official_hold_remaining_minutes")) or 0
            reason_parts.append(
                "Descida bloqueada pela janela de segurança após informação oficial: "
                +f"restam aproximadamente {remaining:.0f} min das "
                +str(DEESCALATION_OFFICIAL_HOLD_HOURS)
                +" h mínimas"
            )
        elif deescalation.get("blocked_by_source_gap"):
            blocked=", ".join(deescalation.get("blocked_reasons") or [])
            reason_parts.append(
                "Rebaixamento retido por evidência insuficiente"
                +((": "+blocked) if blocked else "")
            )
        else:
            reason_parts.append(
                "Normalização em confirmação: "
                +str(deescalation.get("consecutive_confirmations") or 0)
                +"/"+str(DEESCALATION_CONFIRMATIONS)
                +" verificações válidas para o próximo nível"
            )
    elif deescalation.get("pending"):
        reason_parts.append(
            "Nível reduzido uma faixa; a próxima redução exigirá "
            +str(DEESCALATION_CONFIRMATIONS)
            +" novas verificações válidas se a melhora persistir"
        )


    reason=". ".join(x.rstrip(".") for x in reason_parts if x)+"."

    now=datetime.now(TZ)
    payload={
      "schema_version":3,
      "generated_at":now.isoformat(),
      "location":{"city":"Petrópolis","state":"RJ","country":"Brasil"},
      "overall":{
          "level":overall,
          "label":LEVEL_LABELS[overall],
          "reason":reason,
          "candidate_level":candidate,
          "official_candidate_level":official_candidate,
          "supplemental_observation":supplemental_observation,
          "supplemental_signals":supplemental_signals,
          "deescalation":deescalation,
          "rule":"Escalada imediata somente por fonte oficial com status ok. Informação antiga não provoca nova subida. O risco Hidrológico é prioritário para o HST; quando o CEMADEN Hidrológico ultrapassa sua janela de 24 h sem nova atualização, o Bingen - Geo pode atuar como evidência local de normalização, desde que esteja recente e abaixo de 20 mm no curto prazo e 50 mm/24 h, sem outro pluviômetro recente acima desses gatilhos, sem aviso INMET, sem previsão forte e sem sinal operacional recente da Defesa Civil. O risco Geológico antigo não congela indefinidamente o rebaixamento, mas volta a participar imediatamente quando atualizado. Defesa Civil de Petrópolis pode elevar por estágio ou sinal operacional oficial recente. CEMADEN-RJ + INMET, ambos atuais e em nível >=3, podem elevar +1. Após uma informação oficial relevante, o nível não pode cair por 2 horas. Encerrada essa janela sem nova informação de mesmo peso ou maior, são exigidas 3 verificações válidas consecutivas, separadas por ciclos reais de monitoramento, para reduzir apenas uma faixa. Novas publicações oficiais relevantes reiniciam as 2 horas; reler o mesmo aviso não reinicia o relógio. Depois da primeira queda, não há nova espera de 2 horas: são necessárias 3 novas verificações válidas para cada faixa seguinte, desde que não haja agravamento."
      },
      "sources":{"cemaden_geological":geo,"cemaden_hydrological":hydro,"inmet_alerts":inmet,"defesa_civil":defesa},
      "weather":weather,
      "weather_reference":weather_reference,
      "weather_map":weather_map,
      "pluviometers":pluviometers,
      "forecast":forecast,
      "roads":roads,
      "notifications":{
          "group_operational_email":{
              "channel":"email",
              "label":"Grupo Operacional",
              "levels":[3,4,5],
              "recipient_configured":bool(os.getenv("HST_EMAIL_GRUPO_OPERACIONAL","").strip()),
              "recipient_count":len([x for x in re.split(r"[,;\\n]+",os.getenv("HST_EMAIL_GRUPO_OPERACIONAL","")) if x.strip()]),
              "provider_configured":bool(
                  os.getenv("HST_SMTP_USER","").strip()
                  and os.getenv("HST_SMTP_APP_PASSWORD","").strip()
              ),
              "provider":"smtp" if (
                  os.getenv("HST_SMTP_USER","").strip()
                  and os.getenv("HST_SMTP_APP_PASSWORD","").strip()
              ) else None,
              "automatic_sending_enabled":os.getenv("HST_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim"),
              "status":"active" if (
                  os.getenv("HST_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim")
                  and os.getenv("HST_EMAIL_GRUPO_OPERACIONAL","").strip()
              ) else "ready_disabled" if os.getenv("HST_EMAIL_GRUPO_OPERACIONAL","").strip() else "awaiting_recipient"
          },
          "group_managers_email":{
              "channel":"email",
              "label":"Grupo de Gerentes",
              "levels":[4,5],
              "recipient_configured":bool(os.getenv("HST_EMAIL_GRUPO_GERENTES","").strip()),
              "recipient_count":len([x for x in re.split(r"[,;\\n]+",os.getenv("HST_EMAIL_GRUPO_GERENTES","")) if x.strip()]),
              "provider_configured":bool(
                  os.getenv("HST_SMTP_USER","").strip()
                  and os.getenv("HST_SMTP_APP_PASSWORD","").strip()
              ),
              "provider":"smtp" if (
                  os.getenv("HST_SMTP_USER","").strip()
                  and os.getenv("HST_SMTP_APP_PASSWORD","").strip()
              ) else None,
              "automatic_sending_enabled":os.getenv("HST_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim"),
              "status":"active" if (
                  os.getenv("HST_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim")
                  and os.getenv("HST_EMAIL_GRUPO_GERENTES","").strip()
              ) else "ready_disabled" if os.getenv("HST_EMAIL_GRUPO_GERENTES","").strip() else "awaiting_recipient"
          }
      },
      "integrations":{"cemaden_rj":"active","defesa_civil_petropolis":defesa.get("status","source_unconfirmed"),"inmet_alerts":"active","inmet_forecast":forecast.get("status","unavailable"),"inmet_weather":weather.get("status","unavailable"),"weather_reference":weather_reference.get("status","source_unconfirmed"),"weather_map":weather_map.get("status","source_unconfirmed"),"radar":(weather_map.get("radar") or {}).get("status","unavailable"),"pluviometers":pluviometers.get("status","unavailable"),"roads":roads.get("status","unavailable"),"utilities":"pending"}
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    persist_history(payload)
    persist_climate_history(weather,weather_reference)
    print(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
