"""
db_fondos_scraper.py  —  v2
===========================
La página de Deutsche Bank NO hace llamadas AJAX al cambiar gestora:
todo el catálogo está en el HTML inicial y un script JS filtra la vista.

PASO 1 — Diagnóstico (ejecutar primero siempre):
    python db_fondos_scraper.py --probe
    → descarga la página con requests y determina qué modo usar

PASO 2a — Si --probe dice "MODO: static":
    python db_fondos_scraper.py --modo static

PASO 2b — Si --probe dice "MODO: playwright":
    pip install playwright && playwright install chromium
    python db_fondos_scraper.py --modo playwright

Salida: db_fondos_raw.json  y  (con --diff) db_fondos_diff.json
"""

import requests
import json
import time
import argparse
from bs4 import BeautifulSoup

PAGE_URL = "https://www.deutsche-bank.es/es/particulares/ahorro-inversion/productos/documentacion-legal.html"
DOC_BASE = "https://www.servicios.deutsche-bank.es/documentacionfondos/getDocumento"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}


# ─────────────────────────────────────────────────────────────────────────────
# PASO 1: SONDA — determina qué modo usar
# ─────────────────────────────────────────────────────────────────────────────

def probe():
    """
    Descarga la página con requests (sin ejecutar JS) y busca señales de datos.
    Imprime un diagnóstico y recomienda el modo de scraping a usar.
    """
    print(f"Descargando {PAGE_URL} ...")
    resp = requests.get(PAGE_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html  = resp.text
    soup  = BeautifulSoup(html, "html.parser")

    # 1. ¿Hay <select> con las gestoras?
    selects = soup.find_all("select")
    options_gestora = []
    for sel in selects:
        opts = sel.find_all("option")
        if len(opts) > 5:
            options_gestora = opts
            break

    print("\n── Combo de gestoras ─────────────────────────")
    if options_gestora:
        print(f"  ✓ {len(options_gestora)} opciones encontradas")
        for o in options_gestora[:6]:
            print(f"    value='{o.get('value','')}' → {o.get_text(strip=True)}")
        print("    ...")
    else:
        print("  ✗ No encontrado (puede requerir JS para renderizarse)")

    # 2. ¿Hay ISINs en el HTML?
    isin_texts = [t.strip() for t in soup.find_all(string=True)
                  if t and len(t.strip()) == 12
                  and t.strip()[:2] in ("LU","IE","FR","DE","GB","ES","US","AT","NL","BE")]
    print("\n── Datos de fondos en el HTML ─────────────────")
    if isin_texts:
        print(f"  ✓ {len(isin_texts)} ISINs detectados")
        print(f"    Muestra: {isin_texts[:5]}")
    else:
        print("  ✗ No se detectan ISINs — los datos los inyecta JavaScript")

    # 3. ¿Hay JSON con datos en algún <script>?
    kw = {"fondo", "isin", "gestora", "kiid", "coddb"}
    json_scripts = [s for s in soup.find_all("script")
                    if s.string and any(k in s.string.lower() for k in kw)]
    print("\n── JSON embebido en <script> ──────────────────")
    if json_scripts:
        print(f"  ✓ {len(json_scripts)} script(s) con datos de fondos")
        for s in json_scripts:
            snippet = s.string[:300].replace("\n", " ")
            print(f"    {snippet}...")
    else:
        print("  ✗ Ningún script con datos de fondos embebidos")

    # Diagnóstico final
    print("\n── RECOMENDACIÓN ──────────────────────────────")
    if options_gestora and isin_texts:
        print("  MODO: static")
        print("  Los datos están en el HTML estático. Ejecuta:")
        print("    python db_fondos_scraper.py --modo static")
    elif options_gestora and json_scripts:
        print("  MODO: static  (datos en <script> JSON)")
        print("  Ejecuta --modo static; el parser intentará extraer el JSON.")
    else:
        print("  MODO: playwright")
        print("  El HTML lo genera JavaScript. Ejecuta:")
        print("    pip install playwright && playwright install chromium")
        print("    python db_fondos_scraper.py --modo playwright")

    with open("db_probe_debug.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("\n  HTML completo guardado en db_probe_debug.html")
    print("  Ábrelo en el editor y busca 'ISIN' o 'LU0' para confirmar.")


# ─────────────────────────────────────────────────────────────────────────────
# MODO STATIC — todos los datos están en el HTML inicial
# ─────────────────────────────────────────────────────────────────────────────

def scrape_static() -> list[dict]:
    resp = requests.get(PAGE_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return parse_fondos_html(resp.text)


def parse_fondos_html(html: str) -> list[dict]:
    soup   = BeautifulSoup(html, "html.parser")
    fondos = []

    # Intento 1: tabla con cabecera que contenga "ISIN" o "Código"
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        if any("isin" in h or "código" in h for h in headers):
            for row in t.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) >= 4:
                    fondos.append(_cols_to_fondo(cols, row))
            if fondos:
                return fondos

    # Intento 2: elementos con atributo data-isin
    for el in soup.select("[data-isin]"):
        fondos.append({
            "cod_db":  el.get("data-cod-db", ""),
            "nombre":  el.get_text(strip=True),
            "isin":    el.get("data-isin", ""),
            "gestora": el.get("data-gestora", ""),
            "enlaces": [],
        })

    # Intento 3: JSON embebido en <script>
    if not fondos:
        for s in soup.find_all("script"):
            if s.string and "isin" in s.string.lower():
                try:
                    # Buscar el array/objeto JSON dentro del script
                    text = s.string
                    start = text.find("[{")
                    if start == -1:
                        start = text.find("{")
                    if start != -1:
                        data = json.loads(text[start:text.rfind("]") + 1])
                        if isinstance(data, list):
                            fondos = data
                            break
                except Exception:
                    pass

    return fondos


def _cols_to_fondo(cols, row) -> dict:
    f = {
        "cod_db":  cols[0].get_text(strip=True),
        "nombre":  cols[1].get_text(strip=True),
        "isin":    cols[2].get_text(strip=True),
        "gestora": row.get("data-gestora", ""),
        "enlaces": [],
    }
    for a in cols[3].find_all("a", href=True):
        f["enlaces"].append({"tipo": a.get_text(strip=True), "url": a["href"]})
    return f


# ─────────────────────────────────────────────────────────────────────────────
# MODO PLAYWRIGHT — datos generados por JavaScript
# ─────────────────────────────────────────────────────────────────────────────

def scrape_playwright() -> list[dict]:
    """
    Abre la página con Chromium headless, itera cada gestora del combo
    y extrae la tabla de resultados visible en cada iteración.
    """
    from playwright.sync_api import sync_playwright

    todos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()

        print(f"Cargando {PAGE_URL} ...")
        page.goto(PAGE_URL, wait_until="networkidle", timeout=30000)

        # Localizar el combo de gestoras (ajustar selector si hace falta)
        select_el = page.locator("select").first
        options   = select_el.locator("option").all()
        gestoras  = [(o.get_attribute("value"), o.inner_text().strip())
                     for o in options if o.get_attribute("value")]

        print(f"  {len(gestoras)} gestoras en el combo")

        for value, nombre in gestoras:
            print(f"→ {nombre} ...")
            select_el.select_option(value=value)

            # Pequeña espera para que el JS actualice la tabla
            page.wait_for_timeout(600)

            # Capturar el HTML del área de resultados
            # Ajustar el selector al contenedor real si es necesario
            try:
                html_zona = page.locator(
                    "table, #resultados, .resultados-fondos, [class*='result']"
                ).first.inner_html()
            except Exception:
                html_zona = page.content()

            fondos_gestora = parse_fondos_html(f"<table>{html_zona}</table>")
            for f in fondos_gestora:
                if not f.get("gestora"):
                    f["gestora"] = nombre

            todos.extend(fondos_gestora)
            print(f"   {len(fondos_gestora)} fondos")
            time.sleep(0.3)

        browser.close()

    return todos


# ─────────────────────────────────────────────────────────────────────────────
# ENRIQUECIMIENTO — URLs derivadas del ISIN
# ─────────────────────────────────────────────────────────────────────────────

def enrich(fondo: dict) -> dict:
    isin = fondo.get("isin", "")
    if isin:
        fondo["urls_derivadas"] = {
            "KIID": f"{DOC_BASE}?codDoc={isin}&idioma=ES&codSus=KIID&codCont=NA",
            "MECO": f"{DOC_BASE}?codDoc={isin}&idioma=ES&codSus=MECO&codCont=NA",
            "FGES": f"{DOC_BASE}?codDoc={isin}&idioma=ES&codSus=FGES&codCont=NA",
        }
    return fondo


# ─────────────────────────────────────────────────────────────────────────────
# DIFF — detectar altas y bajas vs Excel existente
# ─────────────────────────────────────────────────────────────────────────────

def load_existing_isins(path: str = "GestorDeFondosSerializado.xlsx") -> set:
    import pandas as pd
    df = pd.read_excel(path)
    return set(df["Código ISIN"].dropna().unique())


def compute_diff(nuevos: list[dict], existentes: set) -> dict:
    nuevos_isins = {f["isin"] for f in nuevos if f.get("isin")}
    return {
        "altas":     sorted(nuevos_isins - existentes),
        "bajas":     sorted(existentes - nuevos_isins),
        "sin_cambio": len(nuevos_isins & existentes),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="Diagnóstico: determina qué modo usar")
    ap.add_argument("--modo", choices=["static", "playwright"],
                    help="Modo de extracción")
    ap.add_argument("--diff", action="store_true",
                    help="Comparar con GestorDeFondosSerializado.xlsx")
    args = ap.parse_args()

    if args.probe:
        probe()
        return

    if not args.modo:
        print("Usa --probe primero para determinar el modo, luego --modo static|playwright")
        return

    print(f"Extrayendo fondos — modo: {args.modo}")
    fondos = scrape_static() if args.modo == "static" else scrape_playwright()
    for f in fondos:
        enrich(f)

    with open("db_fondos_raw.json", "w", encoding="utf-8") as fh:
        json.dump(fondos, fh, ensure_ascii=False, indent=2)
    print(f"\n✓ {len(fondos)} fondos → db_fondos_raw.json")

    if args.diff:
        existentes = load_existing_isins()
        diff = compute_diff(fondos, existentes)
        with open("db_fondos_diff.json", "w", encoding="utf-8") as fh:
            json.dump(diff, fh, ensure_ascii=False, indent=2)
        print(f"\n── DIFF ──────────────────────")
        print(f"  Altas:      {len(diff['altas'])}")
        print(f"  Bajas:      {len(diff['bajas'])}")
        print(f"  Sin cambio: {diff['sin_cambio']}")


if __name__ == "__main__":
    main()
