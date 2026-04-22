#!/usr/bin/env python3
"""
scrape_finn.py - Hent info fra finn.no-annonse og lagre til apartments.md

Bruk:
    python3 scrape_finn.py <finn.no URL>

Eksempel:
    python3 scrape_finn.py "https://www.finn.no/realestate/homes/ad.html?finnkode=452511168"

Kjorer du samme URL igjen oppdateres oppforingen istedenfor aa legge til duplikat.

Merk: Kollektivtransport-tider (Trikk/Buss/T-bane) lastes dynamisk via JavaScript
og er ikke tilgjengelig med vanlig HTTP-henting. Legg dem til manuelt om onsket.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

OUTPUT_FILE = Path(__file__).parent / "apartments.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # omit 'br' — requests can't decode brotli
    "Connection": "keep-alive",
}

# finn.no uses stable data-testid attributes on every structured element.
# These map directly to the price fields we want.
PRICE_TESTIDS = {
    "pricing-total-price":         "Totalpris",
    "pricing-registration-charge": "Omkostninger",
    "pricing-joint-debt":          "Fellesgjeld",
    "pricing-common-monthly-cost": "Felleskost/mnd",
    "pricing-collective-assets":   "Fellesformue",
    "pricing-tax-value":           "Formuesverdi",
}

# data-testid -> display label for property info fields
INFO_TESTIDS = {
    "info-property-type":     "Boligtype",
    "info-ownership-type":    "Eieform",
    "info-bedrooms":          "Soverom",
    "info-rooms":             "Rom",
    "info-usable-i-area":     "BRA-i",
    "info-usable-area":       "BRA",
    "info-usable-e-area":     "BRA-e",
    "info-floor":             "Etasje",
    "info-construction-year": "Byggeår",
    "info-plot-area":         "Tomteareal",
    "info-open-area":         "Balkong",
}

# The info elements sometimes render label+value concatenated as text.
# These prefixes let us strip the label part to get just the value.
INFO_LABEL_PREFIXES = {
    "info-property-type":     "Boligtype",
    "info-ownership-type":    "Eieform",
    "info-bedrooms":          "Soverom",
    "info-rooms":             "Rom",
    "info-usable-i-area":     "Internt bruksareal",
    "info-usable-area":       "Bruksareal",
    "info-usable-e-area":     "Eksternt bruksareal",
    "info-floor":             "Etasje",
    "info-construction-year": "Byggeår",
    "info-plot-area":         "Tomteareal",
    "info-open-area":         "Balkong/Terrasse",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    """Replace non-breaking spaces and strip whitespace."""
    return text.replace("\xa0", " ").strip()


def dd_value(container) -> str:
    """Extract the <dd> text inside a data-testid container."""
    if container is None:
        return ""
    dd = container.find("dd")
    return clean(dd.get_text(strip=True)) if dd else ""


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------

def extract_finnkode(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    if "finnkode" not in qs:
        raise ValueError(f"Fant ingen finnkode i URL: {url}")
    return qs["finnkode"][0]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_page(url: str):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    resp = requests.get(url, headers=HEADERS, timeout=20, verify=False)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------

def scrape(soup: BeautifulSoup) -> dict:
    result: dict = {}

    # Title
    h1 = soup.find("h1")
    if h1:
        result["Tittel"] = clean(h1.get_text(strip=True))

    # Address
    addr_el = soup.find(attrs={"data-testid": "object-address"})
    if addr_el:
        result["Adresse"] = clean(addr_el.get_text(strip=True))

    # Prisantydning is NOT inside the <dl>; it has its own <div>
    pris_el = soup.find(attrs={"data-testid": "pricing-incicative-price"})
    if pris_el:
        spans = pris_el.find_all("span")
        if len(spans) >= 2:
            result["Prisantydning"] = clean(spans[-1].get_text(strip=True))

    # Other price fields: <div data-testid="..."><dt>label</dt><dd>value</dd></div>
    for testid, label in PRICE_TESTIDS.items():
        val = dd_value(soup.find(attrs={"data-testid": testid}))
        if val:
            result[label] = val

    # Property info fields: same dt/dd pattern
    for testid, label in INFO_TESTIDS.items():
        el = soup.find(attrs={"data-testid": testid})
        if el:
            val = dd_value(el)
            if not val:
                # Sometimes label and value are concatenated in the text; strip label prefix
                full = clean(el.get_text(strip=True))
                prefix = INFO_LABEL_PREFIXES.get(testid, "")
                if prefix and full.startswith(prefix):
                    val = full[len(prefix):].strip()
            if val:
                result[label] = val

    # Salgsoppgave link
    for a in soup.find_all("a", href=True):
        if "komplett salgsoppgave" in a.get_text(strip=True).lower():
            result["Salgsoppgave"] = a["href"]
            break
    if "Salgsoppgave" not in result:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "prospekt" in href and "aktiv.no" in href and "openSalesStatement" not in href:
                result["Salgsoppgave"] = href
                break

    # Solgt badge
    for el in soup.find_all(True):
        if el.get_text(strip=True) == "Solgt" and "badge" in " ".join(el.get("class", [])):
            result["Solgt"] = "Ja"
            break

    # Visning (viewing date)
    visning_btn = soup.find(attrs={"data-testid": "add-viewing-to-calendar"})
    if visning_btn:
        visning_span = visning_btn.find("span", class_=lambda c: c and "s-text" in c)
        if visning_span:
            txt = clean(visning_span.get_text(strip=True))
            # Text is like "Visning - 26. apr.  kl 13:15 - 14:00"
            result["Visning"] = txt

    # Annonseinformasjon: FINN-kode, Sist endret, Referanse
    info_el = soup.find(attrs={"data-testid": "object-info"})
    if info_el:
        for row in info_el.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) == 2:
                key = clean(cells[0].get_text(strip=True)).lower()
                val = clean(cells[1].get_text(strip=True))
                if "finn" in key and "kode" in key:
                    result["FINN-kode (annonse)"] = val
                elif "sist endret" in key:
                    result["Sist endret"] = val
                elif "referanse" in key:
                    result["Referanse"] = val

    return {k: v for k, v in result.items() if v}


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def format_section(data: dict, finnkode: str, url: str) -> str:
    title = data.get("Tittel", f"Leilighet {finnkode}")
    address = data.get("Adresse", "")

    lines = [f"## {title}"]
    if address:
        lines.append(f"*{address}*")
    lines += [
        "",
        f"**URL:** {url}  ",
        f"**FINN-kode:** {finnkode}  ",
        f"**Hentet:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
    ]

    price_fields = [
        "Prisantydning", "Totalpris", "Fellesgjeld",
        "Felleskost/mnd", "Omkostninger", "Fellesformue", "Formuesverdi",
    ]
    price_rows = [f"| {f} | {data[f]} |" for f in price_fields if f in data]
    if price_rows:
        lines += ["", "### Pris", "| Felt | Verdi |", "| --- | --- |"] + price_rows

    info_fields = [
        "Boligtype", "Eieform", "Rom", "Soverom",
        "BRA-i", "BRA", "BRA-e", "Etasje", "Byggeår",
        "Energimerking", "Tomteareal",
    ]
    info_rows = [f"| {f} | {data[f]} |" for f in info_fields if f in data]
    if info_rows:
        lines += ["", "### Boliginfo", "| Felt | Verdi |", "| --- | --- |"] + info_rows

    if "Salgsoppgave" in data:
        lines += [
            "",
            "### Salgsoppgave",
            f"- [Se komplett salgsoppgave]({data['Salgsoppgave']}) _(ikke analysert enda)_",
        ]

    meta_fields = ["Sist endret", "Referanse", "FINN-kode (annonse)"]
    meta_rows = [f"- **{f}:** {data[f]}" for f in meta_fields if f in data]
    if meta_rows:
        lines += ["", "### Annonseinformasjon"] + meta_rows

    lines += ["", "---", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File update
# ---------------------------------------------------------------------------

def update_file(section: str, finnkode: str) -> str:
    if OUTPUT_FILE.exists():
        content = OUTPUT_FILE.read_text(encoding="utf-8")
    else:
        content = "# Leilighetsjakt\n\n"

    needle = f"**FINN-kode:** {finnkode}"

    if needle in content:
        pos = content.index(needle)
        start = content.rfind("\n## ", 0, pos)
        if start == -1:
            start = content.find("## ", 0, pos)
        else:
            start += 1  # skip leading newline
        end = content.find("\n---\n", pos)
        if end != -1:
            end += len("\n---\n")
        else:
            end = len(content)
        new_content = content[:start] + section + content[end:]
        action = "Oppdatert"
    else:
        new_content = content.rstrip("\n") + "\n\n" + section
        action = "Lagt til"

    OUTPUT_FILE.write_text(new_content, encoding="utf-8")
    return action


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hent finn.no-annonse og lagre/oppdater i apartments.md",
    )
    parser.add_argument("url", help="finn.no URL med finnkode-parameter")
    args = parser.parse_args()
    url = args.url.strip()

    try:
        finnkode = extract_finnkode(url)
    except ValueError as exc:
        print(f"Feil: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Henter finnkode {finnkode} ...")

    try:
        soup = fetch_page(url)
    except requests.HTTPError as exc:
        print(f"HTTP-feil: {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"Nettverksfeil: {exc}", file=sys.stderr)
        sys.exit(1)

    data = scrape(soup)

    if not data:
        print("Advarsel: ingen data ble hentet fra siden.", file=sys.stderr)
        sys.exit(1)

    section = format_section(data, finnkode, url)
    action = update_file(section, finnkode)

    print(f"{action} -> {OUTPUT_FILE}\n")

    for key in ["Tittel", "Adresse", "Prisantydning", "Fellesgjeld",
                "Felleskost/mnd", "BRA-i", "Rom", "Soverom"]:
        if key in data:
            print(f"  {key:20s}: {data[key]}")


if __name__ == "__main__":
    main()
