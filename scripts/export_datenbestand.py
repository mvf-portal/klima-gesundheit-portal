#!/usr/bin/env python3
"""Zieht den gesamten Datenbestand des Hubs aus index.html und dem Archiv.

Erzeugt eine JSON-Gesamtdatei und drei Tabellen fuer Excel. Bewusst ein
Erzeuger und keine Kopie: Eine abgelegte Momentaufnahme veraltet still,
sobald jemand eine Datenbank ergaenzt. Dieses Skript liest immer den
aktuellen Stand.

    py scripts/export_datenbestand.py [Zielordner]

Ohne Zielordner wird nach export/ geschrieben.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

INDEX = "index.html"
ARCHIV = "studien-archiv.json"


def js_array(quelle: str, name: str) -> list:
    """Ein JS-Array-Literal aus index.html als Python-Daten lesen.

    Die Konstanten stehen als JS im Dokument, nicht als JSON: Schluessel ohne
    Anfuehrungszeichen. Deshalb erst nach JSON umschreiben.
    """
    m = re.search(r"const " + name + r" = (\[.*?\n?\]);", quelle, re.DOTALL)
    if not m:
        raise RuntimeError(f"{name} nicht in {INDEX} gefunden")
    roh = m.group(1)
    # Zeilenkommentare raus - zwischen den Eintraegen stehen Rubrik-Ueberschriften.
    roh = re.sub(r"^\s*//[^\n]*$", "", roh, flags=re.MULTILINE)
    # Schluessel wie  c:"deutsch"  ->  "c":"deutsch"
    roh = re.sub(r"([{,\[]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', roh)

    # Einfach gesetzte Zeichenketten auf doppelte umstellen. Der DOAJ-Eintrag
    # nutzt sie, weil seine Adresse selbst doppelte Anfuehrungszeichen enthaelt.
    def einfach_zu_doppelt(m: re.Match) -> str:
        inhalt = m.group(1).replace('\\"', '"').replace('"', '\\"').replace("\\'", "'")
        return ':"' + inhalt + '"'
    roh = re.sub(r":\s*'((?:[^'\\]|\\.)*)'", einfach_zu_doppelt, roh)
    # JavaScript erlaubt ein Komma vor der schliessenden Klammer, JSON nicht.
    roh = re.sub(r",(\s*[}\]])", r"\1", roh)
    return json.loads(roh)


def kategorien(cats: list) -> dict:
    return {c["id"]: c["name"] for c in cats}


def main() -> int:
    ziel = sys.argv[1] if len(sys.argv) > 1 else "export"
    os.makedirs(ziel, exist_ok=True)

    quelle = io.open(INDEX, encoding="utf-8").read()
    cats = js_array(quelle, "CATS")
    db = js_array(quelle, "DB")
    glossar = js_array(quelle, "GLOSSAR")
    chips = js_array(quelle, "CHIPS")
    studien = js_array(quelle, "STUDIES")
    archiv = json.load(io.open(ARCHIV, encoding="utf-8"))
    katname = kategorien(cats)

    BOOL_TEXT = {1: "geprueft: wertet aus", 0: "geprueft: wertet nicht aus"}
    TYP_TEXT = {"live": "Live-Suche", "portal": "Portal", "lic": "Lizenz noetig"}

    gesamt = {
        "erzeugt": dt.datetime.now().isoformat(timespec="seconds"),
        "quelle": "https://klima.m-vf.de/ - Repo mvf-portal/klima-gesundheit-portal",
        "hinweis": "Erzeugt von scripts/export_datenbestand.py aus index.html "
                   "und studien-archiv.json. Nicht von Hand pflegen.",
        "zusammenfassung": {
            "rubriken": len(cats),
            "datenbanken": len(db),
            "live_suchen": sum(1 for x in db if x["t"] == "live"),
            "portale": sum(1 for x in db if x["t"] == "portal"),
            "lizenzpflichtig": sum(1 for x in db if x["t"] == "lic"),
            "boolesch_geprueft_ja": sum(1 for x in db if x.get("b") == 1),
            "boolesch_geprueft_nein": sum(1 for x in db if x.get("b") == 0),
            "glossarbegriffe": len(glossar),
            "schnellwahl": len(chips),
            "studien_aktuell": len(studien),
            "studien_archiv": len(archiv),
        },
        "rubriken": cats,
        "datenbanken": db,
        "glossar": glossar,
        "schnellwahl": chips,
        "studien_aktuell": studien,
        "studien_archiv": archiv,
    }

    p = os.path.join(ziel, "datenbestand.json")
    json.dump(gesamt, io.open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  {p}")

    def tabelle(datei: str, kopf: list, zeilen: list) -> None:
        pfad = os.path.join(ziel, datei)
        buf = io.StringIO(newline="")
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow(kopf)
        w.writerows(zeilen)
        # BOM + Semikolon: so oeffnet deutsches Excel die Datei direkt richtig.
        io.open(pfad, "w", encoding="utf-8-sig", newline="").write(buf.getvalue())
        print(f"  {pfad}  ({len(zeilen)} Zeilen)")

    tabelle("datenbanken.csv",
            ["Rubrik", "Name", "Art", "Boolesche Operatoren", "Deutschsprachig",
             "Beschreibung", "Adresse"],
            [[katname.get(x["c"], x["c"]), x["n"], TYP_TEXT.get(x["t"], x["t"]),
              BOOL_TEXT.get(x.get("b"), "ungeprueft"),
              "ja" if (x["c"] == "deutsch" or x.get("de") == 1) else "nein",
              x["d"], x["u"]] for x in db])

    tabelle("glossar.csv", ["Deutsch", "Englisch"],
            [[g["de"], g["en"]] for g in glossar])

    tabelle("studienarchiv.csv",
            ["Aufgenommen", "Autor", "Publiziert am", "In PubMed seit", "Journal",
             "Jahr", "Titel", "Fragestellung", "Ergebnis", "PMID"],
            [[e.get("aufgenommen", ""), e.get("author", ""), e.get("pubdate", ""),
              e.get("added", ""), e.get("journal", ""), e.get("year", ""),
              e.get("title", ""), e.get("sum", ""), e.get("result", ""), e.get("pmid", "")]
             for e in archiv])

    print("\nZusammenfassung:")
    for k, v in gesamt["zusammenfassung"].items():
        print(f"  {k:<24} {v:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
