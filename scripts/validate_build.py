#!/usr/bin/env python3
import json
import os
import re
from bs4 import BeautifulSoup

REQUIRED_IDS = {
    "hero","levelNum","levelLabel","levelReason","updatedAt","nextAt",
    "geoCard","hidroCard","inmetCard","dcCard","cemadenHealth","inmetHealth",
    "dcHealth","roadsHealth","pluvioBody","historyBody","historyCount",
    "historyOldest","historyNewest","historyMonths","exportHistory"
}

def load_json(path):
    with open(path,"r",encoding="utf-8") as f:
        return json.load(f)

with open("index.html","r",encoding="utf-8") as f:
    html=f.read()
soup=BeautifulSoup(html,"html.parser")
ids={el.get("id") for el in soup.find_all(attrs={"id":True})}
missing=sorted(REQUIRED_IDS-ids)
if missing:
    raise SystemExit("IDs obrigatórios ausentes: "+", ".join(missing))

status=load_json("data/status.json")
level=(status.get("overall") or {}).get("level")
if level not in (1,2,3,4,5):
    raise SystemExit(f"Nível HST inválido: {level}")
for key in ("cemaden_geological","cemaden_hydrological","inmet_alerts","defesa_civil"):
    if key not in (status.get("sources") or {}):
        raise SystemExit(f"Fonte ausente: {key}")
if "pluviometers" not in status or "forecast" not in status or "roads" not in status:
    raise SystemExit("Blocos operacionais obrigatórios ausentes no status.json")

history=load_json("data/history.json")
snaps=history.get("snapshots",[])
if not isinstance(snaps,list) or not snaps:
    raise SystemExit("Histórico sem snapshots")
if any(s.get("level") not in (1,2,3,4,5) for s in snaps if isinstance(s,dict)):
    raise SystemExit("Histórico contém nível HST inválido")

index=load_json("data/history_index.json")
months=index.get("months",[])
if not isinstance(months,list) or not months:
    raise SystemExit("Índice mensal do histórico vazio")
for m in months:
    path=m.get("file")
    if not path or not os.path.exists(path):
        raise SystemExit(f"Arquivo mensal inexistente: {path}")
    archive=load_json(path)
    if not archive.get("snapshots"):
        raise SystemExit(f"Arquivo mensal sem snapshots: {path}")

print(f"VALIDATION_OK level={level} history={len(snaps)} months={len(months)}")
