import re, requests
from bs4 import BeautifulSoup
url="https://resources.cemaden.gov.br/graficos/interativo/grafico_CEMADEN.php?idpcd=3323&menu=periodo&uf=RJ"
r=requests.get(url,timeout=25,headers={"User-Agent":"Mozilla/5.0"})
print("STATUS",r.status_code,"LEN",len(r.text),"TYPE",r.headers.get("content-type"))
soup=BeautifulSoup(r.text,"html.parser")
print("SCRIPTS")
for s in soup.find_all("script"):
    src=s.get("src")
    if src: print(src)
print("POSSIVEIS ENDPOINTS")
for pat in [r'https?://[^"\'\s]+',r'[^"\']+\.php[^"\']*',r'[^"\']+\.json[^"\']*']:
    for x in re.findall(pat,r.text):
        if any(k in x.lower() for k in ("pcd","cemaden","json","acum","estacao","grafico")):
            print(x[:500])
