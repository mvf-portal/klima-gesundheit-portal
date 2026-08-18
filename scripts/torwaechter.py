#!/usr/bin/env python3
"""Prueft eine fertige Newsletter-Ausgabe, bevor sie terminiert wird.

Warum es diese Datei gibt: Der Versand ist der einzige Schritt der ganzen
Kette, der sich nicht zuruecknehmen laesst. Seit dem 18.08.2026 terminiert
`mailchimp_entwurf.py` die Kampagne selbst, statt auf eine Freigabe von Hand zu
warten - der Preis dafuer ist diese Pruefung.

**Was sie leistet und was nicht.** Sie faengt *mechanischen* Unfug: fehlende
Felder, stehengebliebene Platzhalter, erfundene Zeitschriften, englisch
gebliebene Zusammenfassungen, doppelte Studien, ein leeres Empfaengersegment.
Sie faengt *nicht* die Zusammenfassung, die fluessig klingt und die Studie
falsch wiedergibt - dafuer gibt es das Veto-Fenster zwischen Terminierung und
Versand. Wer hier eine Pruefung ergaenzt, sollte sich vorher fragen, in welche
der beiden Klassen sie faellt; die zweite ist maschinell nicht zu haben.

Schlaegt auch nur eine Pruefung an, wird **nicht terminiert**. Der Entwurf
bleibt liegen, und der Grund steht in versand-status.json. Lieber ein Tag ohne
Newsletter als ein falscher - so hat der Herausgeber es am 18.08.2026 entschieden.

Alleine aufrufbar, dann prueft sie den aktuellen Archivstand ohne Mailchimp:
    py scripts/torwaechter.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request

ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Woran eine deutsche Zusammenfassung zu erkennen ist. Ein englisch gebliebener
# Abstract enthaelt keines dieser Woerter - das ist der billigste zuverlaessige
# Test, den es dafuer gibt.
DEUTSCH = re.compile(r"\b(der|die|das|und|wurde|wurden|zeigte|zeigten|bei|"
                     r"nicht|eine|einer|einem|Studie|Patienten)\b")
# Das Ergebnisfeld muss etwas aussagen - aber NICHT zwingend eine Zahl
# enthalten. Qualitative Interviewstudien und Expertenpapiere sind seit dem
# 18.08.2026 ausdruecklich zugelassen (Entscheidung des Herausgebers); eine
# Ziffernpflicht haette an diesem Tag zwei von fuenf Hubs gestoppt, obwohl mit
# den Ausgaben nichts verkehrt war. Geprueft wird deshalb auf Substanz, nicht
# auf Zahlen: Ein Ergebnisfeld mit "n. a." oder drei Woertern faellt durch,
# eine ausformulierte qualitative Kernaussage nicht.
MIN_ERGEBNIS = 60
PFLICHT = ("pmid", "title", "sum", "result", "journal", "year")


def _esummary(pmids: list[str]) -> dict:
    """Zeitschrift und Jahr direkt bei PubMed nachschlagen."""
    daten = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(pmids),
                                    "retmode": "json"}).encode()
    with urllib.request.urlopen(ESUMMARY, data=daten, timeout=60) as r:
        return json.load(r).get("result", {})


def pruefe(studien: list[dict], html: str = "", empfaenger: int | None = None,
           gegen_pubmed: bool = True) -> list[str]:
    """Alle Beanstandungen als Liste. Leere Liste heisst: terminieren."""
    m: list[str] = []
    if not studien:
        return ["keine Studien in der Ausgabe"]

    for i, e in enumerate(studien, 1):
        kopf = f"Studie {i} (PMID {e.get('pmid', '?')})"
        for feld in PFLICHT:
            if not str(e.get(feld, "")).strip():
                m.append(f"{kopf}: Feld '{feld}' ist leer")
        if not str(e.get("pmid", "")).isdigit():
            m.append(f"{kopf}: PMID ist keine Zahl")
        text = " ".join(str(e.get(f, "")) for f in ("title", "sum", "result"))
        if "{{" in text or "}}" in text:
            m.append(f"{kopf}: unersetzter Platzhalter im Text")
        if len(str(e.get("result", "")).strip()) < MIN_ERGEBNIS:
            m.append(f"{kopf}: Ergebnisfeld ist mit "
                     f"{len(str(e.get('result', '')).strip())} Zeichen zu duenn")
        if len(str(e.get("sum", ""))) < 80:
            m.append(f"{kopf}: Zusammenfassung ist verdaechtig kurz "
                     f"({len(str(e.get('sum', '')))} Zeichen)")
        if not DEUTSCH.search(str(e.get("sum", ""))):
            m.append(f"{kopf}: Zusammenfassung enthaelt kein deutsches Wort - "
                     f"vermutlich der englische Abstract")
        if not 20 <= len(str(e.get("title", ""))) <= 200:
            m.append(f"{kopf}: Titellaenge ausserhalb 20-200 Zeichen")

    pmids = [str(e.get("pmid", "")) for e in studien]
    doppelt = {p for p in pmids if pmids.count(p) > 1}
    if doppelt:
        m.append(f"dieselbe Studie mehrfach in der Ausgabe: {', '.join(sorted(doppelt))}")

    # Zeitschrift und Jahr gegen PubMed. Bis August 2026 stammten beide aus der
    # Modellantwort und waren entsprechend geraten - aus NPJ Prim Care Respir
    # Med wurde Nat Commun. Die Quelle ist seither fetch_meta(); diese Pruefung
    # sorgt dafuer, dass ein Rueckfall auffaellt, bevor er versendet wird.
    if gegen_pubmed and all(p.isdigit() for p in pmids):
        try:
            res = _esummary(pmids)
            for e in studien:
                d = res.get(str(e["pmid"]))
                if not d or "error" in d:
                    m.append(f"PMID {e['pmid']}: bei PubMed nicht auffindbar")
                    continue
                echt = (d.get("source") or "").strip().lower()
                ist = str(e.get("journal", "")).strip().lower()
                if echt and ist and echt != ist:
                    m.append(f"PMID {e['pmid']}: Zeitschrift '{e['journal']}' "
                             f"stimmt nicht mit PubMed ('{d.get('source')}')")
                jahr = (d.get("pubdate") or "")[:4]
                if jahr and str(e.get("year", "")) not in (jahr, ""):
                    m.append(f"PMID {e['pmid']}: Jahr {e.get('year')} statt {jahr}")
        except Exception as fehler:              # Netz weg, PubMed langsam
            m.append(f"Abgleich mit PubMed nicht moeglich: {fehler}")

    if html:
        if "{{" in html:
            m.append("unersetzter Platzhalter im Newsletter-HTML")
        if len(html) < 2000:
            m.append(f"Newsletter-HTML ist mit {len(html)} Zeichen zu kurz")
        fehlend = [e["pmid"] for e in studien if str(e.get("pmid", "")) not in html]
        if fehlend:
            m.append(f"im HTML fehlen Studien: {', '.join(map(str, fehlend))}")

    if empfaenger is not None and empfaenger < 1:
        m.append("das Empfaengersegment ist leer - niemand wuerde die Ausgabe bekommen")

    return m


def main() -> int:
    try:
        alle = json.load(open("studien-archiv.json", encoding="utf-8"))
    except FileNotFoundError:
        print("studien-archiv.json nicht gefunden - im Portalverzeichnis aufrufen.")
        return 2
    tage = sorted({e.get("aufgenommen") or e.get("added") for e in alle}, reverse=True)
    neueste = [e for e in alle
               if (e.get("aufgenommen") or e.get("added")) == tage[0]] if tage else []
    m = pruefe(neueste)
    print(f"{len(neueste)} Studien vom {tage[0] if tage else '?'} geprueft.")
    for x in m:
        print("  ! " + x)
    print("Ergebnis:", "TERMINIEREN" if not m else "GESTOPPT")
    return 0 if not m else 1


if __name__ == "__main__":
    sys.exit(main())
