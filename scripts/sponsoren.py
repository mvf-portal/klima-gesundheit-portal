#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Die Sponsoren dieses Portals - eine Quelle fuer Seite, Newsletter und Downloads.

Gepflegt werden sie an genau einer Stelle: im SPONSOR-Block der `index.html`.
Dieses Modul liest sie von dort, damit Newsletter und Download-Dateien
dieselbe Liste verwenden wie die Seite. Zwei Listen waeren eine Liste zu viel -
die zweite ist irgendwann die veraltete.

Dass ein Python-Skript die Angaben aus der `index.html` nachliest, ist im Haus
bereits ueblich: `vorschaltseite.py` zaehlt die Datenbanken genauso, statt sie
abzuschreiben.

Fehlt der Block oder ist die Liste leer, kommt eine leere Liste zurueck - und
jede aufrufende Stelle laesst den Hinweis dann einfach weg.
"""
from __future__ import annotations

import pathlib
import re

BLOCK = re.compile(r"SPONSOR-BLOCK-START.*?SPONSOR-BLOCK-ENDE", re.S)
# Ein Eintrag: {n:"...", logo:"...", u:"..."} - Schluessel ohne Anfuehrungszeichen,
# wie ueberall in dieser Datei. Die Reihenfolge der Felder liegt fest, weil
# neues-portal.py sie erzeugt.
EINTRAG = re.compile(r'\{\s*n:"([^"]*)"\s*,\s*logo:"([^"]*)"\s*,\s*u:"([^"]*)"\s*\}')


def lade(basis: str | pathlib.Path = ".") -> list[dict]:
    """Alle Sponsoren des Portals. Leere Liste, wenn es keine gibt."""
    seite = pathlib.Path(basis) / "index.html"
    if not seite.exists():
        return []
    m = BLOCK.search(seite.read_text(encoding="utf-8"))
    if not m:
        return []
    return [{"n": n, "logo": logo, "u": u} for n, logo, u in EINTRAG.findall(m.group(0))]


def namen(basis: str | pathlib.Path = ".") -> str:
    """Die Namen als Fliesstext: 'A', 'A und B', 'A, B und C'."""
    n = [s["n"] for s in lade(basis)]
    if not n:
        return ""
    if len(n) == 1:
        return n[0]
    return ", ".join(n[:-1]) + " und " + n[-1]


def zeile(basis: str | pathlib.Path = ".") -> str:
    """Der fertige Satz fuer Fusszeilen - leer, wenn es keinen Sponsor gibt.

    Der Zusatz in Klammern ist keine Hoeflichkeit, sondern die Zusage, die das
    Sternchen auf der Seite gibt. Wer sie dort macht, muss sie ueberall machen.
    """
    n = namen(basis)
    return f"Gesponsert von {n} (ohne Einfluss auf die Inhalte)." if n else ""


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ort = sys.argv[1] if len(sys.argv) > 1 else "."
    for s in lade(ort):
        print(f'{s["n"]:<30} {s["logo"]:<28} {s["u"]}')
    print("Fusszeile:", zeile(ort) or "(kein Sponsor - Hinweis entfaellt)")
