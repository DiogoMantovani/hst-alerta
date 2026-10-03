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
    channels={
        "emergency":"199",
        "sms":"40199",
        "whatsapp":DEFESA_CIVIL_WHATSAPP,
        "bulletin":DEFESA_CIVIL_BOLETIM,
        "home":DEFESA_CIVIL_HOME,
    }
    try:
        home=requests.get(DEFESA_CIVIL_HOME,timeout=8,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
        home.raise_for_status()
        tag=requests.get(DEFESA_CIVIL_TAG,timeout=8,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
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
            if len(links)>=4:
                break

        latest_stage=None
        latest_news=None
        for url in links:
            try:
                r=requests.get(url,timeout=4,headers={"User-Agent":"Mozilla/5.0 HST-Alerta/1.0"})
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
            "status":"no_recent_update",
            "stage":None,
            "risk":"Sem atualização oficial recente",
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
            "status":"source_unconfirmed",
            "stage":None,
            "risk":"Sem informação oficial recente confirmada",
            "level":None,
            "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
            "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
            "channels":channels,
            "collected_at":now.isoformat(),
            "url":DEFESA_CIVIL_HOME,
            "error":str(exc)[:500],
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
DEESCALATION_CONFIRMATIONS=3

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

def apply_deescalation_hysteresis(candidate_level, previous, core_sources):
    """Escalation is immediate; de-escalation is gradual and source-aware."""
    prev_overall=(previous.get("overall") or {}) if isinstance(previous,dict) else {}
    try:
        previous_level=int(prev_overall.get("level") or 1)
    except Exception:
        previous_level=1

    state=prev_overall.get("deescalation") or {}
    complete=all(
        isinstance(s,dict) and s.get("status")=="ok" and isinstance(s.get("level"),int)
        for s in core_sources
    )

    if candidate_level>=previous_level:
        return candidate_level,{
            "pending":False,
            "target_level":None,
            "consecutive_confirmations":0,
            "confirmations_required":DEESCALATION_CONFIRMATIONS,
            "blocked_by_source_gap":False,
        },False

    target=max(candidate_level,previous_level-1)
    if not complete:
        return previous_level,{
            "pending":True,
            "target_level":target,
            "consecutive_confirmations":0,
            "confirmations_required":DEESCALATION_CONFIRMATIONS,
            "blocked_by_source_gap":True,
        },True

    previous_target=state.get("target_level")
    previous_count=int(state.get("consecutive_confirmations") or 0)
    count=previous_count+1 if previous_target==target else 1

    if count>=DEESCALATION_CONFIRMATIONS:
        return target,{
            "pending":False,
            "target_level":None,
            "consecutive_confirmations":0,
            "confirmations_required":DEESCALATION_CONFIRMATIONS,
            "blocked_by_source_gap":False,
        },False

    return previous_level,{
        "pending":True,
        "target_level":target,
        "consecutive_confirmations":count,
        "confirmations_required":DEESCALATION_CONFIRMATIONS,
        "blocked_by_source_gap":False,
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
    roads=fetch_roads(previous)

    usable=[s for s in (geo,hydro,inmet) if isinstance(s.get("level"),int)]
    official_candidate=max([s["level"] for s in usable], default=1)

    # Corroboration between independent official providers can escalate one level.
    cemaden_level=max([s.get("level") or 0 for s in (geo,hydro)])
    inmet_level=inmet.get("level") if isinstance(inmet.get("level"),int) else 0
    escalated=False
    candidate=official_candidate
    if cemaden_level>=3 and inmet_level>=3:
        candidate=min(5,max(cemaden_level,inmet_level)+1)
        escalated=True

    # Forecast and pluviometers are early-warning evidence only.
    supplemental_signals=supplemental_observation_signals(forecast,pluviometers)
    supplemental_observation=bool(supplemental_signals)
    if supplemental_observation and candidate<2:
        candidate=2

    overall,deescalation,deescalation_held=apply_deescalation_hysteresis(
        candidate,previous,(geo,hydro,inmet)
    )

    driver_floor=max(1,candidate-(1 if escalated else 0))
    top=[]
    for s in (geo,hydro,inmet):
        if isinstance(s.get("level"),int) and s["level"]>=driver_floor and s["level"]>1:
            top.append(f'{s["name"]}: {s.get("risk")}')

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

    if deescalation_held:
        if deescalation.get("blocked_by_source_gap"):
            reason_parts.append("Rebaixamento retido por indisponibilidade ou defasagem de fonte oficial")
        else:
            reason_parts.append(
                "Rebaixamento aguardando "
                +str(deescalation.get("consecutive_confirmations") or 0)
                +"/"+str(DEESCALATION_CONFIRMATIONS)
                +" coletas consecutivas de melhora"
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
          "rule":"Escalada imediata pelo maior nível oficial válido; CEMADEN-RJ + INMET em nível >=3 podem elevar +1. Previsão de chuva forte/intensa e pluviometria elevada podem levar somente a Observação (2). Rebaixamento ocorre um nível por vez após 3 coletas consecutivas válidas de melhora e não ocorre com lacuna de fonte oficial."
      },
      "sources":{"cemaden_geological":geo,"cemaden_hydrological":hydro,"inmet_alerts":inmet},
      "weather":weather,
      "weather_reference":weather_reference,
      "weather_map":weather_map,
      "pluviometers":pluviometers,
      "forecast":forecast,
      "roads":roads,
      "notifications":{
          "group_operational_test":{
              "channel":"whatsapp",
              "levels":[3,4,5],
              "recipient_configured":bool(os.getenv("HST_WPP_GRUPO_OPERACIONAL_TESTE","").strip()),
              "recipient_masked":("•••• "+os.getenv("HST_WPP_GRUPO_OPERACIONAL_TESTE","").strip()[-4:]) if os.getenv("HST_WPP_GRUPO_OPERACIONAL_TESTE","").strip() else None,
              "provider_configured":all([
                  os.getenv("HST_WPP_ACCESS_TOKEN","").strip(),
                  os.getenv("HST_WPP_PHONE_NUMBER_ID","").strip(),
                  os.getenv("HST_WPP_TEMPLATE_NAME","").strip(),
                  os.getenv("HST_WPP_GRAPH_VERSION","").strip(),
              ]),
              "status":"ready" if all([
                  os.getenv("HST_WPP_GRUPO_OPERACIONAL_TESTE","").strip(),
                  os.getenv("HST_WPP_ACCESS_TOKEN","").strip(),
                  os.getenv("HST_WPP_PHONE_NUMBER_ID","").strip(),
                  os.getenv("HST_WPP_TEMPLATE_NAME","").strip(),
                  os.getenv("HST_WPP_GRAPH_VERSION","").strip(),
              ]) else "awaiting_provider" if os.getenv("HST_WPP_GRUPO_OPERACIONAL_TESTE","").strip() else "recipient_missing"
          }
      },
      "integrations":{"cemaden_rj":"active","inmet_alerts":"active","inmet_forecast":forecast.get("status","unavailable"),"inmet_weather":weather.get("status","unavailable"),"weather_reference":weather_reference.get("status","source_unconfirmed"),"weather_map":weather_map.get("status","source_unconfirmed"),"radar":(weather_map.get("radar") or {}).get("status","unavailable"),"pluviometers":pluviometers.get("status","unavailable"),"roads":roads.get("status","unavailable"),"utilities":"pending"}
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    persist_history(payload)
    persist_climate_history(weather,weather_reference)
    print(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
