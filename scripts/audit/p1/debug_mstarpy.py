import mstarpy
import requests

# Monkey-patch para ver llamadas reales de mstarpy
original_get  = requests.get
original_post = requests.post

def patched_get(url, **kwargs):
    print(f"GET {url[:120]}")
    hdrs = kwargs.get("headers", {})
    for k, v in list(hdrs.items())[:4]:
        print(f"    {k}: {str(v)[:80]}")
    r = original_get(url, **kwargs)
    print(f"    status={r.status_code}  len={len(r.text)}")
    if r.status_code == 200 and len(r.text) > 5:
        print(f"    body={r.text[:300]}")
    return r

def patched_post(url, **kwargs):
    print(f"POST {url[:120]}")
    r = original_post(url, **kwargs)
    print(f"    status={r.status_code}  len={len(r.text)}")
    return r

requests.get  = patched_get
requests.post = patched_post

print("Instanciando fondo...")
try:
    fund = mstarpy.Funds(term="LU0438336694", language="en-gb", pageSize=1)
    print("OK — fondo instanciado")
    print("Llamando information()...")
    info = fund.information()
    if isinstance(info, dict):
        print(f"Keys: {list(info.keys())}")
        for k, v in info.items():
            if any(x in k.lower() for x in ["bench", "index", "name", "categ", "strategy"]):
                print(f"  {k}: {str(v)[:120]}")
    else:
        print(f"Tipo: {type(info)}  valor: {str(info)[:200]}")
except Exception as e:
    print(f"Error: {e}")
