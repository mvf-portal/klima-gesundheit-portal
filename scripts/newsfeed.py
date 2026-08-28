#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Newsfeed: aktuelle Beitraege aus dem MVF-Archiv zum Thema dieses Hubs.

Angeregt am 26.08.2026 von einem Nutzer der Hubs, zusammen mit dem
Ausschreibungsradar: Wer sich fuer ein Thema interessiert, will nicht nur die
Studienlage, sondern auch, was dazu geschrieben wurde.

**Quelle ist das eigene Archiv** (m-vf.de, 8.596 Beitraege in der Rubrik News)
- Entscheidung des Herausgebers vom 28.08.2026. Fremdmedien bewusst nicht: Das
Presseleistungsschutzrecht (Paragraphen 87f bis 87h UrhG) erlaubt nur
Ueberschrift, Link und kleinste Textausschnitte, und ein Feed mit Tagespresse
verwaesserte das Evidenz-Profil der Hubs.

Gesucht wird ueber die WordPress-Suche, nicht ueber ein Sprachmodell - wie in
`knowledge-hubs/scripts/verwandtes.py`, an dem sich dieses Skript orientiert.
Ein Modell, das Beitragsadressen nennen darf, erfindet sie irgendwann. Deshalb
kostet der Newsfeed auch nichts und darf taeglich laufen.

Am 28.08.2026 gemessen, und daran haengen zwei Entscheidungen:

  * **`orderby=relevance`, nicht `orderby=date`.** Nach Datum sortiert liefert
    die Suche Beitraege, in denen der Begriff nur am Rand vorkommt: Zu
    "Adipositas" standen die vier neuesten Treffer allesamt in
    Studien-Sammelmeldungen. Nach Relevanz sortiert kommen fachlich passende
    Beitraege - und weil das Archiv taeglich waechst, sind sie meist ohnehin
    aktuell. Das Datumsfenster (`NICHT_AELTER_ALS`) haelt Uraltes heraus.
  * **Die eigenen Tagesmeldungen fliegen raus.** "25 neue Studien in elf
    Knowledge-Hubs aufgenommen" ist kein Fachbeitrag, sondern dieser Hub, der
    ueber sich selbst berichtet. Erkannt werden sie am festen Vorspann der
    Pipeline (`WP_AUSZUG` in tagesnews.py) - nicht am Titel: Der lautet
    manchmal "Hospizzugang, Arzneimittelkaskaden und KI-Prognosen: 75 Studien"
    und traegt das Wort Knowledge-Hub gar nicht.
  * **Der Suchbegriff muss vorn stehen.** Die Suche durchsucht den ganzen
    Beitrag; ein Wort im vorletzten Absatz macht daraus keine Meldung zum
    Thema. Genommen wird nur, was den Begriff in Titel oder Auszug traegt
    (`trifft`).

Gesucht wird nach `NEWS_SUCHE` aus `scripts/thema.py`; fehlt die Liste, nimmt
das Skript die Schnellwahlbegriffe der Seite (Marker-Block CHIPS).

Aufruf:
    python scripts/newsfeed.py            # holen und Block setzen
    python scripts/newsfeed.py --probe    # nur zeigen, nichts schreiben
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WP = "https://www.monitor-versorgungsforschung.de/wp-json/wp/v2"
# Die MVF-Seite beantwortet Standard-Skriptkennungen mit HTTP 403. Jede Anfrage
# braucht eine eigene - dieselbe Falle wie in tagesnews.py und verwandtes.py.
UA = "MVF-Knowledge-Hubs/1.0 (+https://knowledge-hubs.m-vf.de)"

SEITE = "index.html"
START = "// === NEWS-BLOCK-START (taeglich von GitHub Actions ersetzt) ==="
ENDE = "// === NEWS-BLOCK-ENDE ==="

# So viele Beitraege stehen auf der Seite. Sechs fuellen die Karte, ohne die
# Studien zu verdraengen - die bleiben das Herzstueck des Hubs.
ANZEIGEN_MAX = 6
# Je Suchbegriff so viele Treffer holen. Mehr braucht es nicht: Genommen wird
# reihum, damit jeder Begriff vorkommt.
JE_BEGRIFF = 4
# Aelteres kommt nicht in einen Feed, der "aktuell" heisst. Zwei Jahre sind
# grosszuegig genug, dass auch enge Themen etwas finden.
NICHT_AELTER_ALS = 730
# Der feste Vorspann der eigenen Tagesmeldungen (tagesnews.py, WP_AUSZUG).
EIGENE_MELDUNG = "Aus der Forschung frisch auf den Schreibtisch"


def begriffe_holen(text: str) -> tuple[list[str], str]:
    """Wonach dieser Hub im Archiv sucht - und woher die Begriffe stammen.

    Erste Wahl ist `NEWS_SUCHE` in `scripts/thema.py`. Zweite Wahl sind die
    Schnellwahlbegriffe (Marker-Block CHIPS): Sie sind je Hub vom Herausgeber
    ausgesucht und ersparen eine zweite Pflegestelle.

    Warum es die erste Wahl trotzdem gibt: Chips sind fuer Datenbankabfragen
    gemacht, nicht fuer eine Archivsuche. Am 28.08.2026 im Gender-Hub
    gemessen - dort stehen "Herzinfarkt" und "Arzneimittelsicherheit" als
    Chips, weil deren geschlechtsspezifische Seite das Thema ist. Im Archiv
    holen dieselben Woerter allgemeine Herz- und Arzneimittelmeldungen. Fuer
    Hubs, deren Thema eine Perspektive ist und kein Gegenstand, gehoert
    deshalb eine eigene Liste in thema.py.
    """
    try:
        from thema import NEWS_SUCHE          # noqa: PLC0415 - optional
        eigene = [b.strip() for b in NEWS_SUCHE if b and b.strip()]
        if eigene:
            return eigene, "thema.py (NEWS_SUCHE)"
    except ImportError:
        pass
    return chips(text), "Schnellwahl (CHIPS)"


def chips(text: str) -> list[str]:
    """Die Schnellwahlbegriffe aus dem Marker-Block CHIPS."""
    m = re.search(r"const CHIPS = (\[.*?\]);", text, re.S)
    if not m:
        return []
    # Die Liste ist JS mit erlaubtem Komma am Ende - das mag json nicht.
    roh = re.sub(r",(\s*\])", r"\1", m.group(1))
    try:
        return [b.strip() for b in json.loads(roh) if b and b.strip()]
    except ValueError:
        return []


def suche(begriff: str, ab: str) -> list[dict]:
    """Beitraege zu einem Begriff, nach Relevanz. Leere Liste statt Ausnahme.

    Eine fehlgeschlagene Suche darf den Hub nicht aufhalten: Der Block bleibt
    dann stehen, wie er ist, und die Seite zeigt die Beitraege von gestern.
    """
    ziel = (f"{WP}/posts?search={urllib.parse.quote(begriff)}"
            f"&per_page={JE_BEGRIFF}&orderby=relevance&after={ab}"
            f"&_fields=id,title,link,date,excerpt")
    try:
        req = urllib.request.Request(ziel, headers={"User-Agent": UA,
                                                    "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, ValueError, TimeoutError) as fehler:
        print(f"  Suche nach '{begriff}' fehlgeschlagen: {fehler}")
        return []


def klartext(beitrag: dict) -> str:
    """Titel und Auszug eines Beitrags als reiner Text."""
    roh = (beitrag.get("title", {}).get("rendered", "") + " "
           + beitrag.get("excerpt", {}).get("rendered", ""))
    return unescape(re.sub(r"<[^>]+>", " ", roh))


def trifft(beitrag: dict, begriff: str) -> bool:
    """Kommt der Suchbegriff in Titel oder Auszug vor?

    Die WordPress-Suche durchsucht den ganzen Beitrag. Ein Wort, das im
    vorletzten Absatz einmal faellt, macht die Meldung aber nicht zu einer
    Meldung ueber dieses Thema. Am 28.08.2026 im Gender-Hub gemessen: Die Suche
    nach "sex differences" brachte "DANK: Zuckersteuer nicht auf die lange Bank
    schieben", die nach "gender medicine" eine Meldung ueber Wegovy.

    Geprueft wird jedes Wort ab vier Zeichen einzeln: "Pflegende Angehoerige"
    trifft auch, wenn im Titel nur von "pflegenden Angehoerigen" die Rede ist.
    Kurze Woerter (und, mit, bei) bleiben aussen vor - sie treffen immer.
    """
    text = klartext(beitrag).casefold()
    woerter = [w for w in re.findall(r"\w+", begriff.casefold())
               if len(w) >= 4]
    if not woerter:
        return begriff.casefold() in text
    # Wortstamm statt ganzem Wort: Deutsche Beugung haengt hinten an.
    return any(w[:max(4, len(w) - 2)] in text for w in woerter)


def eigen(beitrag: dict) -> bool:
    """Berichtet dieser Beitrag ueber die Knowledge-Hubs selbst?

    Zwei Merkmale, weil eines nicht reicht: Die taeglichen Studienmeldungen
    tragen den festen Vorspann der Pipeline, aber am 28.08.2026 stand im
    Pflege-Hub auch "MVF-Knowledge-Hubs machen Studiensuche leichter" im Feed -
    ein redaktioneller Beitrag ueber die Hubs, ohne diesen Vorspann. Ein Hub,
    der sich selbst als Nachricht zeigt, ist eine Schleife.
    """
    auszug = unescape(re.sub(r"<[^>]+>", "",
                             beitrag.get("excerpt", {}).get("rendered", "")))
    titel = unescape(beitrag.get("title", {}).get("rendered", ""))
    return EIGENE_MELDUNG in auszug or "knowledge-hub" in titel.casefold()


def sammle(begriffe: list[str], heute: dt.date) -> list[dict]:
    """Zu allen Begriffen die besten Treffer - reihum, ohne Dubletten.

    Reihum je ein Treffer pro Begriff, nicht erst alle zum ersten: Sonst
    stammen bei sieben Begriffen alle sechs Beitraege vom ersten, und die
    uebrigen Themenstraenge des Hubs kaemen gar nicht vor.
    """
    ab = (heute - dt.timedelta(days=NICHT_AELTER_ALS)).isoformat() + "T00:00:00"
    listen = [[b for b in suche(begriff, ab)
               if not eigen(b) and trifft(b, begriff)]
              for begriff in begriffe]
    gefunden: list[dict] = []
    gesehen: set[int] = set()
    for runde in range(JE_BEGRIFF):
        for liste in listen:
            if len(gefunden) >= ANZEIGEN_MAX:
                break
            if runde < len(liste) and liste[runde]["id"] not in gesehen:
                gesehen.add(liste[runde]["id"])
                gefunden.append(liste[runde])
    # Auf der Seite steht das Neueste oben - gesucht wurde nach Relevanz,
    # angezeigt wird nach Datum. Beides zusammen ergibt einen Feed, der
    # thematisch trifft und trotzdem aktuell aussieht.
    gefunden.sort(key=lambda b: b.get("date") or "", reverse=True)
    return gefunden


def block(beitraege: list[dict], stand: str) -> str:
    """Der Marker-Block fuer index.html."""
    def js(s: str) -> str:
        return json.dumps(s or "", ensure_ascii=False)
    zeilen = [START, f'const NEWS_STAND = "{stand}";', "const NEWS = ["]
    for b in beitraege:
        titel = unescape(b.get("title", {}).get("rendered", "")).strip()
        zeilen.append("  {titel:%s, datum:%s, url:%s}," %
                      (js(titel), js((b.get("date") or "")[:10]),
                       js(b.get("link", ""))))
    zeilen += ["];", ENDE]
    return "\n".join(zeilen)


def main() -> int:
    p = argparse.ArgumentParser(description="Newsfeed aus dem MVF-Archiv")
    p.add_argument("--probe", action="store_true", help="nur zeigen")
    a = p.parse_args()

    seite = pathlib.Path(SEITE)
    text = seite.read_text(encoding="utf-8")
    begriffe, woher = begriffe_holen(text)
    if not begriffe:
        raise SystemExit(f"Keine Suchbegriffe gefunden - weder NEWS_SUCHE in "
                         f"scripts/thema.py noch ein CHIPS-Block in {SEITE}.")
    print(f"{len(begriffe)} Suchbegriffe aus {woher}: {', '.join(begriffe)}")

    heute = dt.date.today()
    beitraege = sammle(begriffe, heute)
    print(f"{len(beitraege)} Beitraege:")
    for b in beitraege:
        print(f"  {(b.get('date') or '')[:10]}  "
              f"{unescape(b['title']['rendered'])[:78]}")

    if a.probe:
        print(f"\n[Probe] {SEITE} unveraendert.")
        return 0
    if not beitraege:
        # Lieber die Beitraege von gestern als eine leere Karte: Ein Ausfall
        # der Suche sieht sonst aus wie ein Thema, ueber das nie etwas
        # geschrieben wurde.
        print("Nichts gefunden - Block bleibt unveraendert.")
        return 0

    muster = re.compile(re.escape(START) + r".*?" + re.escape(ENDE), re.DOTALL)
    if not muster.search(text):
        raise SystemExit(f"Marker-Block fehlt in {SEITE} - nichts geaendert.")
    neu = block(beitraege, heute.strftime("%d.%m.%Y"))
    seite.write_text(muster.sub(lambda _: neu, text), encoding="utf-8")
    print(f"{SEITE} aktualisiert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
