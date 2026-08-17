#!/usr/bin/env python3
"""Aktualisiert den Studien-Block in index.html mit den neuesten PubMed-Treffern.

Ablauf:
  1. PubMed E-utilities: zwei Abfragen - die neuesten Treffer allgemein und
     zusaetzlich die mit Deutschlandbezug (MeSH/Affiliation), zusammengefuehrt.
  2. Claude-API: 5-7 Studien auswaehlen, die konkrete Ergebnisse nennen UND auf
     das deutsche Versorgungssystem uebertragbar sind, und auf Deutsch
     zusammenfassen (strukturierte JSON-Ausgabe).
  3. Nur den Marker-Block (SNAP_DATE + STUDIES) in index.html ersetzen.

Bricht mit Exit-Code != 0 ab, wenn etwas fehlschlaegt - dann bleibt index.html
unveraendert und der Workflow schlaegt sichtbar fehl (kein kaputter Commit).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import anthropic
import requests

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# Hitze/Klima UND Gesundheit muessen beide vorkommen - sonst spuelt die Abfrage
# reine Klimaphysik oder reine Klinik herein. Die Gesundheitsseite ist bewusst
# breit gehalten (Sterblichkeit, Morbiditaet, Versorgung), die Umweltseite ueber
# MeSH plus Freitext, weil "heat wave" nicht durchgaengig verschlagwortet ist.
_UMWELT = (
    '("Climate Change"[MeSH Terms] OR "Extreme Heat"[MeSH Terms] '
    'OR "Hot Temperature"[MeSH Terms] OR "Global Warming"[MeSH Terms] '
    'OR "Heat Stress Disorders"[MeSH Terms] OR "Air Pollution"[MeSH Terms] '
    'OR "heat wave"[Title/Abstract] OR heatwave*[Title/Abstract] '
    'OR "extreme heat"[Title/Abstract] OR "urban heat island"[Title/Abstract] '
    'OR "extreme weather"[Title/Abstract] OR "climate change"[Title/Abstract])'
)
_GESUNDHEIT = (
    '("Mortality"[MeSH Terms] OR "Morbidity"[MeSH Terms] '
    'OR "Public Health"[MeSH Terms] OR "Health Services"[MeSH Terms] '
    'OR "Hospitalization"[MeSH Terms] OR "Environmental Health"[MeSH Terms] '
    'OR "Emergency Medical Services"[MeSH Terms] '
    'OR mortality[Title/Abstract] OR morbidity[Title/Abstract] '
    'OR hospitali*[Title/Abstract] OR "health outcome*"[Title/Abstract] '
    'OR "public health"[Title/Abstract] OR "health risk*"[Title/Abstract] '
    'OR "health impact*"[Title/Abstract] OR patients[Title/Abstract])'
)
# "Humans"[MeSH] ist wichtiger, als es aussieht: ohne diese Klammer spuelt die
# Abfrage Algenbluete, Emissionsbilanzen und Tierstudien herein, die formal beide
# Seiten erfuellen. Gemessen am 17.08.2026: rund 89.000 Treffer gesamt,
# 14.600 mit Europa-/Deutschlandbezug - genug Nachschub fuer die Tagesauswahl.
TERM = os.environ.get(
    "SEARCH_TERM",
    f'(({_UMWELT} AND {_GESUNDHEIT}) AND "Humans"[MeSH Terms])',
)
# Zweite Abfrage, damit Arbeiten mit Deutschlandbezug den Kandidatenpool
# sicher erreichen. Ueber MeSH und Autorenadresse, nicht ueber Journalnamen -
# deutschsprachige Journale liefern kaum Treffer.
TERM_DE = os.environ.get(
    "SEARCH_TERM_DE",
    f"{TERM} AND (Germany[MeSH Terms] OR Germany[Affiliation] "
    "OR Europe[MeSH Terms] OR Europe[Affiliation])",
)
MODEL = os.environ.get("MODEL", "claude-haiku-4-5")  # Standard: guenstig; via MODEL-env aenderbar
INDEX = "index.html"

# NCBI bittet bei automatisierten Zugriffen um Tool-Kennung und Kontaktadresse.
NCBI_TOOL = "klima-gesundheit-portal"
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "stegmaier@m-vf.de")

START = "// === STUDIES-BLOCK-START (taeglich 06:00 Uhr von GitHub Actions ersetzt) ==="
END = "// === STUDIES-BLOCK-ENDE ==="
ARCHIVE = "studien-archiv.json"   # Vollstaendige Historie; die Seite laedt sie fuer den Ordner
                                  # "Aeltere Suchergebnisse" nach und blendet die aktuellen aus.

MONTHS = {1: "Jan.", 2: "Feb.", 3: "März", 4: "Apr.", 5: "Mai", 6: "Juni",
          7: "Juli", 8: "Aug.", 9: "Sept.", 10: "Okt.", 11: "Nov.", 12: "Dez."}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["studies"],
    "properties": {
        "studies": {
            "type": "array",
            # **Hier keine Laengenbegrenzung eintragen.** Am 17.08.2026 nacheinander
            # ausprobiert und beide Male mit HTTP 400 abgelehnt:
            #   minItems -> "values other than 0 or 1 are not supported"
            #   maxItems -> "property 'maxItems' is not supported"
            # Die Anzahl wird deshalb in pick_studies() geregelt, nicht im Schema.
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["journal", "year", "pmid", "title", "sum", "result", "transfer"],
                "properties": {
                    "journal": {"type": "string"},
                    "year": {"type": "string"},
                    "pmid": {"type": "string"},
                    "title": {"type": "string"},
                    "sum": {"type": "string"},
                    "result": {"type": "string"},
                    # Kurze Begruendung, warum das Ergebnis auf Deutschland
                    # uebertragbar ist - oder warum nur bedingt. Hier zaehlen
                    # Klimazone und Versorgungsstruktur gleichermassen.
                    "transfer": {"type": "string"},
                },
            },
        }
    },
}

SYSTEM = (
    "Du bist Fachredakteur fuer Klimawandel und Gesundheit / Planetary Health. "
    "Aus einer Liste von PubMed-Abstracts waehlst du die relevantesten aktuellen "
    "Studien aus und fasst sie praezise auf Deutsch zusammen. Deine Leserschaft "
    "arbeitet im deutschen Gesundheitswesen: oeffentlicher Gesundheitsdienst, "
    "Kliniken, Praxen, Kommunen, Gesundheitspolitik."
)

USER_TEMPLATE = """Unten stehen aktuelle PubMed-Abstracts (nach Datum sortiert).

Waehle GENAU 6 Studien aus, die (a) einen erkennbaren Bezug zwischen Umwelt- bzw.
Klimafaktoren und menschlicher Gesundheit haben UND (b) im Abstract KONKRETE
quantitative Ergebnisse nennen (Prozentwerte, relative Risiken, Odds/Hazard Ratios,
zurechenbare Todesfaelle, p-Werte, Fallzahlen). Ueberspringe Studien ohne Abstract
oder ohne konkrete Ergebnisse. Achte auf thematische Vielfalt.

THEMATISCHE RANGFOLGE - in dieser Reihenfolge bevorzugen:
  1. Hitze und Extremtemperatur: Sterblichkeit, Morbiditaet, Notaufnahmen,
     Risikogruppen, Hitzeaktionsplaene, Warnsysteme, Wirksamkeit von Schutzmassnahmen.
  2. Weitere Klimafolgen mit direktem Gesundheitsbezug: Extremwetter, Duerre,
     Ueberschwemmung, Waldbrandrauch, vektor- und wasseruebertragene Krankheiten,
     Pollen und Allergien, psychische Folgen.
  3. Luftqualitaet und Umweltexposition, wenn ein Klimabezug erkennbar ist.
  4. Das Gesundheitswesen selbst als Verursacher und Betroffener: Emissionen von
     Kliniken, Klimaresilienz der Versorgung, Anpassung von Einrichtungen.

Reine Klimaphysik, Emissionsbilanzen ohne Gesundheitsbezug, Tier- und
Pflanzenoekologie gehoeren NICHT in die Auswahl, auch wenn das Wort "health"
im Abstract vorkommt.

ZWEI HARTE REGELN ZUR ZUSAMMENSETZUNG (sie gehen der thematischen Rangfolge vor):

  1. MINDESTENS DREI der sechs Studien muessen Europa, Nordamerika oder eine
     andere gemaessigte Klimazone betreffen. Liegen weniger als drei solche
     Arbeiten vor, nimm die verbleibenden Plaetze aus dem Rest - aber schoepfe
     die europaeischen zuerst aus, auch wenn sie thematisch nur zweitbeste sind.
  2. HOECHSTENS ZWEI der sechs duerfen reine Luftschadstoff-Studien sein
     (Feinstaub, Ozon, Stickoxide). Dieses Feld publiziert um ein Vielfaches
     mehr als die Hitzeforschung und verdraengt sie sonst vollstaendig.

Diese Regeln entstanden aus einem Fehlversuch: Ohne sie bestand die Auswahl aus
Luftverschmutzung in China, Duerre in Brasilien und einer chinesischen Megastadt -
fachlich einwandfrei, fuer eine deutsche Leserschaft aber unbrauchbar.

ZWEITES AUSWAHLKRITERIUM - Übertragbarkeit auf Deutschland:
Bei sonst gleicher Qualität hat die übertragbare Studie IMMER Vorrang vor der
aktuelleren. Übertragbarkeit richtet sich hier nach ZWEI Achsen:

  Klimatisch: Mitteleuropa und gemäßigte Breiten sind übertragbar. Studien aus
    tropischen, ariden oder subtropischen Regionen nur, wenn die Fragestellung
    davon unabhängig ist (Methodik, Warnsysteme, Risikogruppen als Prinzip) -
    absolute Temperaturschwellen und Anpassungsniveaus sind es nie.
  Strukturell: Deutschland, Österreich, Schweiz, Niederlande, Belgien, Frankreich
    hoch; Skandinavien, Großbritannien, Kanada, Australien mittel; USA gering.

  Hoch:    Deutschland und deutschsprachiger Raum, Mitteleuropa.
  Mittel:  Übriges Europa mit gemäßigtem Klima, Kanada, Nordchina, Japan, Korea -
           vergleichbare Klimazone, andere Versorgungsstruktur.
  Gering:  Tropen und Subtropen, Länder mit grundlegend anderer Ressourcenlage.
           Nur nehmen, wenn die Fragestellung klimazonenunabhängig ist.

Eine Studie aus Südasien zur Hitzesterblichkeit gehört nur in die Auswahl, wenn
sonst nichts Brauchbares vorliegt - die Temperaturschwellen und die
Anpassungsfähigkeit der Bevölkerung sind dort nicht mit Deutschland vergleichbar.

Fuer jede Studie:
- journal: Journalname genau so, wie er in der Kopfzeile des Abstracts steht -
  Abkuerzung nicht aufloesen, nichts ergaenzen. (Wird ohnehin durch die Angabe
  aus PubMed ersetzt; rate hier nichts.)
- year: Erscheinungsjahr, z. B. "2026"
- pmid: die PubMed-ID
- title: praegnanter deutscher Titel
- sum: 1 Satz auf Deutsch, was die Studie untersucht hat
- result: Deutsch, die konkreten Zahlen/Befunde + ein kurzer Einordnungssatz.
  Deutsches Zahlenformat mit Komma (z. B. 0,63).
- transfer: EIN Halbsatz (höchstens 12 Wörter), warum das Ergebnis für Deutschland
  taugt - oder wo die Grenze liegt. Nenne Land bzw. Klimazone und Datengrundlage.
  Keine ganzen Sätze, keine Wiederholung des Titels.
  Gut:      "Deutsche Sterbedaten, gemäßigte Klimazone"
            "Südeuropa - höhere Temperaturschwellen als hierzulande"
            "Niederlande, vergleichbares Klima und Versorgungssystem"
            "Tropen - nur die Methodik ist übertragbar"
            "Nur bedingt: Bevölkerung dort besser hitzeangepasst"
  Schlecht: "Diese Studie ist gut übertragbar." (sagt nichts)

WICHTIG - Fachterminologie: Etablierte englische Fachbegriffe NICHT eindeutschen.
Sie sind auch im deutschen Fachdeutsch stehende Begriffe; eine woertliche Uebersetzung
wirkt unprofessionell und erschwert das Wiederfinden. Beispiele fuer Begriffe, die
englisch bleiben: Public Health, Planetary Health, One Health, Urban Heat Island,
Heat Health Action Plan, Screening, Follow-up, Outcome, Exposure, Confounder,
Baseline, Setting, Cluster, Hazard Ratio, Odds Ratio, Attributable Fraction,
Distributed Lag Non-linear Model. Gaengige Abkuerzungen ebenfalls unveraendert
lassen: COPD, ICU, PM2.5, PM10, NO2, UTCI, WBGT, DLNM.
Deutsche Fachbegriffe, die es gibt, aber verwenden: Hitzewelle, Tropennacht,
Uebersterblichkeit, Waermeinsel, Hitzeaktionsplan, Gefuehlte Temperatur,
Zurechenbare Todesfaelle, Risikogruppe.
Faustregel: Wuerde eine deutsche Fachzeitschrift wie Monitor Versorgungsforschung den
Begriff englisch stehen lassen, dann tue es auch. Im Zweifel englisch belassen und bei
Bedarf eine kurze deutsche Erlaeuterung in Klammern ergaenzen.
Umgekehrt gilt: Wo es ein gebraeuchliches deutsches Fachwort gibt (Verweildauer,
Hausarztkontakt, Nutzenbewertung, Fallzahl), dieses verwenden.

Gib ausschliesslich das geforderte JSON zurueck.

=== ABSTRACTS ===
{abstracts}
"""


def _get(path: str, params: dict, timeout: int) -> requests.Response:
    """GET mit drei Versuchen - PubMed ist gelegentlich kurz nicht erreichbar."""
    params = {**params, "tool": NCBI_TOOL, "email": NCBI_EMAIL}
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(f"{EUTILS}/{path}", params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            if attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"PubMed-Abruf fehlgeschlagen ({exc}); neuer Versuch in {wait}s ...")
                time.sleep(wait)
    raise RuntimeError(f"PubMed nicht erreichbar: {last}")


def _suche(term: str, anzahl: int) -> list[str]:
    r = _get(
        "esearch.fcgi",
        {"db": "pubmed", "term": term, "sort": "date",
         "retmax": str(anzahl), "retmode": "json"},
        timeout=30,
    )
    return r.json().get("esearchresult", {}).get("idlist", [])


def fetch_pubmed() -> str:
    """Zwei Abfragen statt einer, zusammengefuehrt und entdoppelt.

    Die allgemeine Abfrage allein reicht nicht - bei diesem Thema sogar noch
    weniger als im Schwesterportal. Klima- und Umweltgesundheit wird weltweit
    stark aus China, Suedasien und Suedamerika publiziert; die tagesaktuellen
    Neuaufnahmen sind entsprechend dominiert. Der erste Lauf am 17.08.2026 mit
    einem Pool aus 40 allgemeinen und 11 europaeischen Treffern lieferte eine
    Auswahl ganz ohne Europabezug: Huaihe-Region, Brasilien, chinesische
    Megastadt. Fuer eine deutsche Leserschaft ist das wertlos - Temperatur-
    schwellen und Anpassungsgrad sind dort nicht vergleichbar.

    Deshalb stellt die Europa-Abfrage inzwischen die MEHRHEIT des Pools und
    steht vorn: Ein Sprachmodell gewichtet, was es zuerst liest. Ueber
    Journalnamen zu suchen bringt uebrigens nichts: im ganzen August ein Treffer.
    """
    europa = _suche(TERM_DE, 30)
    allgemein = _suche(TERM, 25)
    # Reihenfolge: erst Europa, dann der Rest der weltweit neuesten. Wer das
    # umdreht, bekommt wieder eine Auswahl ohne Bezug zu hiesigen Verhaeltnissen.
    ids = europa + [p for p in allgemein if p not in europa]
    if not ids:
        raise RuntimeError("esearch lieferte keine PMIDs")
    print(f"{len(europa)} mit Europa-/Deutschlandbezug + {len(ids) - len(europa)} "
          f"zusaetzlich weltweit = {len(ids)} Kandidaten.")
    r2 = _get(
        "efetch.fcgi",
        {"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "text"},
        timeout=60,
    )
    return r2.text


MONATE_NUM = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
               "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def _datum_teile(roh: str) -> tuple[int, str]:
    """PubMed-Datum -> (Genauigkeit, deutsche Schreibweise).

    Genauigkeit 3 = Tag, 2 = Monat, 1 = Jahr, 0 = unbrauchbar.
    """
    if not roh:
        return (0, "")
    m = re.match(r"^(\d{4})(?:\s+([A-Za-z]{3})[a-z]*)?(?:\s+(\d{1,2}))?", roh.strip())
    if not m:
        return (0, "")
    jahr, mon, tag = m.group(1), m.group(2), m.group(3)
    if mon and mon[:3] in MONATE_NUM:
        nr = MONATE_NUM[mon[:3]]
        if tag:
            return (3, f"{int(tag):02d}.{nr:02d}.{jahr}")
        return (2, f"{MONTHS[nr]} {jahr}")
    return (1, jahr)


def _sortschluessel(e: dict) -> str:
    """ISO-Datum zum Sortieren - aus den Rohfeldern, nicht aus dem Anzeigetext.

    Fehlt der Tag, wird der 1. angenommen: nur zum Sortieren, angezeigt wird
    weiterhin die unvollstaendige Angabe.
    """
    for feld in ("epubdate", "pubdate"):
        m = re.match(r"^(\d{4})(?:\s+([A-Za-z]{3}))?(?:\s+(\d{1,2}))?", (e.get(feld) or "").strip())
        if m:
            jahr = m.group(1)
            mon = MONATE_NUM.get((m.group(2) or "")[:3], 0)
            tag = int(m.group(3) or 1)
            if mon:
                return f"{jahr}-{mon:02d}-{tag:02d}"
    return (e.get("sortpubdate") or "").replace("/", "-")[:10]


def fetch_meta(pmids: list[str]) -> dict[str, dict]:
    """Journal, Jahr, Autor und Publikationsdatum ueber esummary holen.

    Bewusst nicht vom Sprachmodell erraten lassen: Das sind harte Fakten.

    **Das Journal gehoert unbedingt hierher, nicht in die Modellantwort.** Im
    Schwesterportal ki.m-vf.de stand am 17.08.2026 ueber einer Studie aus
    NPJ Prim Care Respir Med der Name Nat Commun, und aus Qual Life Res wurde
    das ausgeschriebene Quality of Life Research. Beides plausibel, beides
    falsch: Der Abstract-Block nennt das Journal nur in der Kopfzeile, und ein
    Sprachmodell ergaenzt dort bereitwillig, was haeufig vorkommt. Eine falsche
    Quellenangabe ist in einem Rechercheportal der teuerste aller kleinen
    Fehler - sie macht die Studie unauffindbar.
    sortpubdate wird ignoriert - PubMed setzt dort bei reinen Monatsangaben
    den 1. ein, was einen Tag vortaeuschen wuerde. Genommen wird die
    genaueste ECHTE Angabe aus pubdate und epubdate.
    """
    r = _get("esummary.fcgi",
             {"db": "pubmed", "retmode": "json", "id": ",".join(pmids)},
             timeout=60)
    roh = r.json().get("result", {})
    aus: dict[str, dict] = {}
    for pmid in pmids:
        e = roh.get(pmid)
        if not e or "error" in e:
            continue
        namen = e.get("authors") or []
        erster = e.get("sortfirstauthor") or (namen[0]["name"] if namen else "")
        autor = f"{erster} et al." if erster and len(namen) > 1 else erster
        genauigkeit, datum = max(_datum_teile(e.get("pubdate", "")),
                                 _datum_teile(e.get("epubdate", "")),
                                 key=lambda x: x[0])
        # Tag, an dem PubMed den Eintrag aufgenommen hat. Danach waehlt esearch
        # aus - er liegt oft Wochen nach dem Erscheinen und erklaert, warum die
        # Publikationsdaten springen.
        aufnahme = ""
        for h in e.get("history", []):
            if h.get("pubstatus") == "entrez":
                d = h.get("date", "")[:10].split("/")
                if len(d) == 3:
                    aufnahme = f"{d[2]}.{d[1]}.{d[0]}"
                break
        eintrag = {"author": autor, "pubdate": datum,
                   "added": aufnahme, "_sort": _sortschluessel(e)}
        # source ist die von PubMed gefuehrte Journal-Abkuerzung. Nur setzen,
        # wenn sie wirklich da ist - eine ungefaehre Angabe ist immer noch
        # besser als eine leere.
        if e.get("source"):
            eintrag["journal"] = e["source"]
        jahr = (datum or "")[-4:]
        if jahr.isdigit():
            eintrag["year"] = jahr
        aus[pmid] = eintrag
    print(f"Metadaten zu {len(aus)}/{len(pmids)} PMIDs geladen.")
    return aus


def pick_studies(abstracts: str) -> list[dict]:
    client = anthropic.Anthropic()
    # Nur strukturierte Ausgabe erzwingen (kein effort/thinking), damit es auch
    # mit guenstigen Modellen wie claude-haiku-4-5 laeuft (die effort/thinking
    # nicht unterstuetzen).
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(abstracts=abstracts)}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    studies = json.loads(text)["studies"]
    # Zu viele ist kein Grund abzubrechen: Die Auswahl ist nach Relevanz
    # geordnet, die vorderen sechs sind brauchbar. Am 17.08.2026 lieferte das
    # Modell trotz "waehle GENAU 6" neun Stueck - und weil das Schema keine
    # Laengenbegrenzung zulaesst (siehe SCHEMA), wird hier gekappt.
    if len(studies) > 7:
        print(f"{len(studies)} Studien geliefert - auf die ersten 6 gekuerzt.")
        studies = studies[:6]
    # Zu wenige dagegen heisst, dass etwas grundsaetzlich schieflief - dann
    # lieber sichtbar scheitern als eine duenne Auswahl veroeffentlichen.
    if len(studies) < 5:
        raise RuntimeError(f"Unerwartete Studienanzahl: {len(studies)}")
    return studies


def build_block(studies: list[dict], status: str = "neu") -> str:
    now = dt.datetime.now(ZoneInfo("Europe/Berlin"))
    snap = f"{now.day}. {MONTHS[now.month]} {now.year}, {now:%H:%M} Uhr"

    def js(v: str) -> str:
        """Sicheres JS-String-Literal MIT HTML-Escaping.

        Zwei Ebenen muessen stimmen: index.html setzt die Werte per innerHTML ein,
        deshalb wird zuerst HTML-maskiert (& < >), damit ein Zeichen wie < im
        generierten Text das Markup nicht zerlegt. json.dumps erzeugt danach ein
        gueltiges JS-Literal (Anfuehrungszeichen, Backslashes, Zeilenumbrueche).
        """
        return json.dumps(html.escape(v, quote=False), ensure_ascii=False)

    items = []
    for s in studies:
        items.append(
            "  {\n"
            f"    journal:{js(s['journal'])}, year:{js(s['year'])}, pmid:{js(s['pmid'])},\n"
            f"    author:{js(s.get('author', ''))}, pubdate:{js(s.get('pubdate', ''))},\n"
            f"    added:{js(s.get('added', ''))},\n"
            f"    title:{js(s['title'])},\n"
            f"    sum:{js(s['sum'])},\n"
            f"    result:{js(s['result'])},\n"
            f"    transfer:{js(s.get('transfer', ''))}\n"
            "  }"
        )
    # SNAP_DATE wird per textContent gesetzt, nicht per innerHTML -> kein HTML-Escaping.
    return (
        f"{START}\n"
        f"const SNAP_DATE = {json.dumps(snap, ensure_ascii=False)};\n"
        # "neu" = die Auswahl hat sich geaendert, "unveraendert" = der Lauf lief,
        # PubMed hatte aber nichts Neues. Die Seite unterscheidet das vom
        # technischen Ausfall - dann bleibt SNAP_DATE einfach alt stehen.
        f"const SNAP_STATUS = {json.dumps(status, ensure_ascii=False)};\n"
        "const STUDIES = [\n"
        + ",\n".join(items)
        + ",\n];\n"
        f"{END}"
    )


def update_archive(studies: list[dict]) -> int:
    """Nimmt die aktuellen Studien ins Archiv auf. Rueckgabe: Gesamtzahl im Archiv.

    Dedupliziert ueber die PMID; das zuerst gesehene Aufnahmedatum bleibt erhalten,
    damit eine Studie nicht bei jedem Lauf nach vorne rutscht.
    """
    try:
        with open(ARCHIVE, encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        entries = []

    known = {e["pmid"] for e in entries}
    heute = dt.datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")
    neu = 0
    for s in studies:
        if s["pmid"] in known:
            continue
        entries.append({
            "pmid": s["pmid"], "journal": s["journal"], "year": s["year"],
            "author": s.get("author", ""), "pubdate": s.get("pubdate", ""),
            "added": s.get("added", ""),
            "transfer": s.get("transfer", ""),
            "title": s["title"], "sum": s["sum"], "result": s["result"],
            "aufgenommen": heute,
        })
        known.add(s["pmid"])
        neu += 1

    entries.sort(key=lambda e: (e["aufgenommen"], e["pmid"]), reverse=True)
    with open(ARCHIVE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    print(f"Archiv: {neu} neu, {len(entries)} gesamt.")
    return len(entries)


def main() -> int:
    abstracts = fetch_pubmed()
    studies = pick_studies(abstracts)
    meta = fetch_meta([s["pmid"] for s in studies])
    for s in studies:
        s.update(meta.get(s["pmid"], {"author": "", "pubdate": "", "added": "", "_sort": ""}))

    # Nach Publikationsdatum absteigend. Vorher bestimmte das Sprachmodell die
    # Reihenfolge - es kann aus einem Abstract kein verlaessliches Datum lesen,
    # weshalb aeltere Studien zwischen neueren standen.
    studies.sort(key=lambda s: s.get("_sort") or "", reverse=True)

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()

    # Hat sich die Auswahl ueberhaupt geaendert? Ein Lauf ohne neue Studien ist
    # kein Fehler - die Seite soll das aber sagen koennen.
    vorher = set(re.findall(r'pmid:"(\d+)"', html))
    jetzt = {s["pmid"] for s in studies}
    status = "neu" if jetzt != vorher else "unveraendert"
    print(f"Auswahl: {status} ({len(jetzt - vorher)} neue PMIDs).")

    for s in studies:
        s.pop("_sort", None)
    block = build_block(studies, status)
    update_archive(studies)

    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(html):
        raise RuntimeError("Marker-Block nicht in index.html gefunden")
    new_html = pattern.sub(lambda _m: block, html, count=1)

    if new_html == html:
        print("Inhalt unveraendert.")
        return 0

    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"index.html aktualisiert: {len(studies)} Studien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
