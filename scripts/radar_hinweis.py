#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Die Radar-Zahl dieses Hubs - geholt statt selbst gesucht.

Der Ausschreibungsradar laeuft seit dem 28.08.2026 einmal zentral im
Versorgungsforschungs-Hub (`scripts/ausschreibungen.py`) und ist dort nach den
zwoelf Themengebieten gegliedert. Dieses Skript laeuft in **allen zwoelf** Hubs
und setzt in `index.html` den RADAR-Block: die Fristen des eigenen Gebiets
(im Versorgungsforschungs-Hub die aller zwoelf) und die Adresse der zentralen
Seite. Die Karte auf der Seite rechnet daraus aus, wie viele Ausschreibungen
heute noch laufen.

Warum die Fristen und nicht bloss eine Zahl: Zwischen dem naechtlichen Lauf und
dem Seitenaufruf koennen Tage liegen - wenn der zentrale Lauf einmal ausfaellt,
sogar mehr. Eine mitgeschriebene Zahl waere dann zu hoch und verspraeche
Ausschreibungen, die es nicht mehr gibt. Aus Fristen laesst sich das im Browser
nachrechnen; dieselbe Vorsicht wie beim Radar selbst.

Warum ueberhaupt zur Bauzeit und nicht per Abruf im Browser: Die Hub-Seiten
sind eigenstaendig - sie holen zur Laufzeit nichts nach. Faellt GitHub aus,
steht der Hub trotzdem.

Aufruf:
    python scripts/radar_hinweis.py            # Block in index.html setzen
    python scripts/radar_hinweis.py --probe    # nur zeigen, nichts schreiben
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = "MVF-Knowledge-Hubs/1.0 (+https://knowledge-hubs.m-vf.de)"
# Der zentrale Radar liegt im Versorgungsforschungs-Hub. Gelesen wird die
# Datei aus dem Repo und nicht von der Netzseite: Sie ist die Quelle, die
# Seite nur ihre Darstellung.
QUELLE = ("https://raw.githubusercontent.com/"
          "mvf-portal/versorgungsforschung-portal/main/ausschreibungen.json")
ZENTRALE = "https://wissen.m-vf.de/ausschreibungen.html"

SEITE = "index.html"
PROFIL = "portal.json"
START = "// === RADAR-BLOCK-START (taeglich von GitHub Actions ersetzt) ==="
ENDE = "// === RADAR-BLOCK-ENDE ==="


def gliederung() -> dict:
    """Die zentrale Datei - lokal, wenn dieser Hub sie selbst schreibt."""
    hier = pathlib.Path("ausschreibungen.json")
    if hier.exists():
        return json.loads(hier.read_text(encoding="utf-8"))
    req = urllib.request.Request(QUELLE, headers={"User-Agent": UA,
                                                  "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def block(gebiete: list[dict], eigenes: dict, stand: str,
          zuhause: bool) -> str:
    """Der RADAR-Block fuer index.html.

    `RADAR_FRISTEN` ist eine Liste je Themengebiet, nicht eine flache Liste:
    Im Versorgungsforschungs-Hub steht der Radar selbst, und die Karte nennt
    dort beides - wie viele Ausschreibungen laufen und aus wie vielen
    Gebieten. Das laesst sich nur zaehlen, wenn die Gebiete getrennt bleiben.
    In den elf anderen Hubs enthaelt die Liste genau einen Eintrag, den des
    eigenen Gebiets.
    """
    def js(wert) -> str:
        return json.dumps(wert, ensure_ascii=False)
    quelle = gebiete if zuhause else [eigenes]
    fristen = [[e["frist"] for e in g.get("ausschreibungen", [])]
               for g in quelle]
    return "\n".join([
        START,
        f"const RADAR_STAND = {js(stand)};",
        f"const RADAR_URL = {js(ZENTRALE + '#' + eigenes['slug'])};",
        # Wahr nur im Versorgungsforschungs-Hub: Dort steht der Radar selbst,
        # und "Besuchen Sie den Hub Versorgungsforschung" waere eine Einladung
        # dorthin, wo der Leser schon ist.
        f"const RADAR_ZUHAUSE = {'true' if zuhause else 'false'};",
        f"const RADAR_FRISTEN = {js(fristen)};",
        ENDE,
    ])


def main() -> int:
    p = argparse.ArgumentParser(description="Radar-Hinweis dieses Hubs")
    p.add_argument("--probe", action="store_true", help="nur zeigen")
    a = p.parse_args()

    profil = json.loads(pathlib.Path(PROFIL).read_text(encoding="utf-8"))
    slug = profil.get("SLUG")
    if not slug:
        raise SystemExit(f"SLUG fehlt in {PROFIL} - nichts geaendert.")

    try:
        daten = gliederung()
    except (urllib.error.URLError, TimeoutError, ValueError) as fehler:
        # Der Block bleibt stehen, wie er ist: Die Zahlen von gestern sind
        # naeher an der Wahrheit als eine geleerte Karte.
        print(f"Zentrale Datei nicht lesbar ({fehler}) - index.html "
              f"unveraendert.")
        return 0

    gebiete = daten.get("themen") or []
    gebiet = next((g for g in gebiete if g.get("slug") == slug), None)
    if gebiet is None:
        raise SystemExit(
            f"Themengebiet '{slug}' steht nicht in der zentralen Datei. "
            f"Fehlt es in scripts/radar_themen.py?")

    zuhause = slug == "versorgungsforschung"
    neu = block(gebiete, gebiet, daten.get("stand", ""), zuhause)
    if zuhause:
        gesamt = sum(len(g.get("ausschreibungen") or []) for g in gebiete)
        print(f"Alle Gebiete: {gesamt} Ausschreibung(en) aus {len(gebiete)} "
              f"Themengebieten, Stand {daten.get('stand', '?')}.")
    else:
        print(f"{gebiet['name']}: {len(gebiet.get('ausschreibungen', []))} "
              f"Ausschreibung(en), Stand {daten.get('stand', '?')}.")
    if a.probe:
        print("\n" + neu + f"\n\n[Probe] {SEITE} unveraendert.")
        return 0

    seite = pathlib.Path(SEITE)
    text = seite.read_text(encoding="utf-8")
    muster = re.compile(re.escape(START) + r".*?" + re.escape(ENDE), re.DOTALL)
    if not muster.search(text):
        raise SystemExit(f"Marker-Block fehlt in {SEITE} - nichts geaendert.")
    seite.write_text(muster.sub(lambda _: neu, text), encoding="utf-8")
    print(f"{SEITE} aktualisiert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
