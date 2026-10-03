import requests
from bs4 import BeautifulSoup

base="https://resources.cemaden.gov.br/graficos/interativo/"
url=base+"grafico_CEMADEN.php?idpcd=3323&menu=periodo&uf=RJ"
r=requests.get(url,timeout=25,headers={"User-Agent":"Mozilla/5.0"})
print("STATUS",r.status_code,"LEN",len(r.text),"TYPE",r.headers.get("content-type"))
p=r.text.find("getJson2.php")
print("GETJSON_CONTEXT")
print(r.text[max(0,p-1800):p+2200] if p>=0 else "not found")

for q in [
    "getJson2.php?uf=RJ",
    "getJson2.php?uf=RJ&idCidade=3303906",
    "getJson2.php?uf=RJ&idcidade=3303906",
]:
    try:
        x=requests.get(base+q,timeout=20,headers={"User-Agent":"Mozilla/5.0","Accept":"application/json,text/plain,*/*"})
        print("TEST",q,"STATUS",x.status_code,"TYPE",x.headers.get("content-type"),"LEN",len(x.text))
        print(x.text[:2500])
    except Exception as e:
        print("TEST_ERROR",q,repr(e))
