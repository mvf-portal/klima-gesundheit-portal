#!/usr/bin/env python3
"""Aktualisiert den Studien-Block in index.html mit den neuesten PubMed-Treffern.

Ablauf:
  1. PubMed E-utilities: zwei Abfragen - die neuesten Treffer allgemein und
     zusaetzlich die mit Deutschlandbezug (MeSH/Affiliation), zusammengefuehrt.
  2. Claude-API: Studien auswaehlen, die konkrete Ergebnisse nennen UND auf
     das deutsche Versorgungssystem uebertragbar sind, und auf Deutsch
     zusammenfassen (strukturierte JSON-Ausgabe). Kriterien: thema.py.
  3. Nur den Marker-Block (SNAP_DATE + STUDIES) in index.html ersetzen.

Bricht mit Exit-Code != 0 ab, wenn etwas fehlschlaegt - dann bleibt index.html
unveraendert und der Workflow schlaegt sichtbar fehl (kein kaputter Commit).
"""
from __future__ import annotations

import argparse
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

# Alles Themenspezifische steht in thema.py - Suchabfrage, Rollenbeschreibung,
# Auswahlregeln, Anzahl. Diese Datei bleibt in allen Portalen wortgleich; wer
# hier etwas korrigiert, kann es mit vorlage-abgleich.py in die Schwesterportale
# uebernehmen. Wer das Thema aendert, aendert thema.py.
from thema import (ANZAHL_MAX, ANZAHL_MIN, ANZAHL_SOLL, EUROPA_ZUERST, KAPPEN,
                   NCBI_TOOL, POOL_ALLGEMEIN, POOL_EUROPA, SYSTEM, TERM, TERM_DE,
                   USER_TEMPLATE)

# GitHub gibt ein Secret genau so weiter, wie es eingefuegt wurde - mit einem
# angehaengten Zeilenumbruch, wenn beim Einfuegen einer mitkam. httpx weigert
# sich dann, den Kopfzeilenwert zu senden, und der Abbruch erscheint als
# "APIConnectionError: Connection error" - also als Netzproblem, das keines ist.
# Beim Longevity-Portal hat das am 18.08.2026 zwei Laeufe gekostet. Deshalb hier
# einmal abschneiden, statt den Fehler spaeter im Secret zu suchen.
_schluessel = os.environ.get("ANTHROPIC_API_KEY", "").strip()
if _schluessel:
    os.environ["ANTHROPIC_API_KEY"] = _schluessel

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MODEL = os.environ.get("MODEL", "claude-haiku-4-5")  # Standard: guenstig; via MODEL-env aenderbar
INDEX = "index.html"

# NCBI bittet bei automatisierten Zugriffen um Tool-Kennung und Kontaktadresse.
# Die Kennung kommt aus thema.py (Repo-Name des jeweiligen Portals).
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
                    # uebertragbar ist - oder warum nur bedingt.
                    "transfer": {"type": "string"},
                },
            },
        }
    },
}

def _get(path: str, params: dict, timeout: int) -> requests.Response:
    """Abfrage mit drei Versuchen - PubMed ist gelegentlich kurz nicht erreichbar.

    **POST, nicht GET.** Die Abfrage steht im Rumpf, nicht in der Adresse. Als
    GET scheitert ein langer Suchausdruck mit HTTP 414 (Request-URI Too Long),
    und zwar erst dann, wenn das Portal schon gebaut ist: Am 25.08.2026 ist der
    erste Lauf des Mental-Hubs genau daran gestorben - zwei Listen von
    Majr-Begriffen plus NOT-Block plus Europa-Zusatz ergaben rund 3.000 Zeichen.
    NCBI nimmt fuer esearch, efetch und esummary ausdruecklich POST entgegen;
    die Antwort ist dieselbe. Der Name bleibt `_get`, damit die Aufrufstellen
    unveraendert bleiben - was er tut, steht hier.
    """
    params = {**params, "tool": NCBI_TOOL, "email": NCBI_EMAIL}
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(f"{EUTILS}/{path}", data=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            if attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"PubMed-Abruf fehlgeschlagen ({exc}); neuer Versuch in {wait}s ...")
                time.sleep(wait)
    raise RuntimeError(f"PubMed nicht erreichbar: {last}")


# Publikationstypen, die nie in eine Ausgabe gehoeren. Eine Berichtigung
# ("Corrigendum to ...") traegt keine eigenen Ergebnisse; das Modell waehlt sie
# trotzdem, wenn sie im Pool liegt, und schreibt dann Platzhalter in die Felder.
# Am 20.08.2026 im Versorgungsforschungs-Portal passiert (PMID 42617323).
# Der Ausschluss steht hier und nicht im Prompt: Was gar nicht erst im Pool
# liegt, kann auch nicht ausgewaehlt werden.
AUSSCHLUSS = ('NOT ("Published Erratum"[pt] OR "Retraction of Publication"[pt] '
              'OR "Retracted Publication"[pt] OR "Duplicate Publication"[pt])')


# Beim Nachtrag wird die Abfrage auf EINEN Tag eingeschnuert. Massgeblich ist
# [EDAT] - der Tag, an dem PubMed die Arbeit aufgenommen hat, nicht das
# Publikationsdatum. Genau dieser Tag ist es, den der taegliche Lauf gesehen
# haette: Er sortiert nach `date`, und das ist die Aufnahme. Ueber [DP] zu
# fenstern ergaebe eine andere, nie dagewesene Auswahl.
NACHTRAG_TAG: str | None = None


def _fenster(term: str) -> str:
    """Die Abfrage, im Nachtrag auf den Aufnahmetag begrenzt."""
    if not NACHTRAG_TAG:
        return term
    d = NACHTRAG_TAG.replace("-", "/")
    return f'({term}) AND ("{d}"[EDAT] : "{d}"[EDAT])'


def bekannte_pmids() -> set[str]:
    """Was schon einmal ausgeliefert wurde - aus dem Archiv."""
    try:
        with open(ARCHIVE, encoding="utf-8") as f:
            return {e["pmid"] for e in json.load(f)}
    except FileNotFoundError:
        return set()


def _suche(term: str, anzahl: int, bekannt: set[str] | None = None) -> list[str]:
    # **Mehr holen, als in den Pool passt, und das Bekannte herauswerfen.**
    # Das Modell weiss nicht, was gestern schon erschienen ist; es waehlt aus
    # dem Pool jedes Mal die staerksten Arbeiten - und das sind an ruhigen
    # Tagen dieselben wie gestern. Anschliessend wirft das Archiv sie als
    # Doppel weg, und uebrig bleibt eine duenne oder leere Ausgabe. Am
    # 26.08.2026 im Gesundheitskompetenz-Hub gemessen: 34 der 51 Arbeiten im
    # Pool waren neu, geliefert wurden trotzdem null - das Modell hatte
    # sechsmal dieselben Bekannten gewaehlt. Seither sieht es nur noch, was
    # es noch nie gesehen hat.
    faktor = 4 if bekannt else 1
    r = _get(
        "esearch.fcgi",
        {"db": "pubmed", "term": f"({_fenster(term)}) {AUSSCHLUSS}", "sort": "date",
         "retmax": str(min(anzahl * faktor, 200)), "retmode": "json"},
        timeout=30,
    )
    treffer = r.json().get("esearchresult", {}).get("idlist", [])
    if bekannt:
        frisch = [p for p in treffer if p not in bekannt]
        if len(frisch) < anzahl and len(treffer) >= anzahl:
            print(f"Nur {len(frisch)} unbekannte von {len(treffer)} Treffern - "
                  f"der Pool faellt entsprechend kleiner aus.")
        treffer = frisch
    return treffer[:anzahl]


def fetch_pubmed() -> str:
    """Zwei Abfragen statt einer, zusammengefuehrt und entdoppelt.

    Die allgemeine Abfrage allein reicht in keinem der Portale. Publiziert wird
    weltweit, mit starkem Uebergewicht der USA und Asiens; die tagesaktuellen
    Neuaufnahmen sind entsprechend dominiert. Fuer eine deutsche Leserschaft
    zaehlt aber, was in einem vergleichbaren Versorgungs- und Rechtsrahmen gilt.

    Deshalb stellt die Europa-Abfrage die MEHRHEIT des Pools und steht vorn:
    Ein Sprachmodell gewichtet, was es zuerst liest. Die Groessen stehen in
    thema.py (POOL_EUROPA, POOL_ALLGEMEIN).

    Beide Abfragen liefern seit dem 26.08.2026 nur Arbeiten, die noch nicht im
    Archiv stehen - siehe _suche(). Der Pool ist damit immer so gross wie
    bestellt, aber garantiert frisch.
    """
    bekannt = bekannte_pmids()
    europa = _suche(TERM_DE, POOL_EUROPA, bekannt)
    allgemein = _suche(TERM, POOL_ALLGEMEIN, bekannt)
    # Reihenfolge: in der Regel erst Europa, dann der Rest der weltweit neuesten.
    # Wer das umdreht, bekommt eine Auswahl ohne Bezug zu hiesigen Verhaeltnissen
    # - im Klima-Portal nachgewiesen. EUROPA_ZUERST steht in thema.py, weil das
    # Versorgungsforschungs-Portal es seit jeher andersherum haelt.
    if EUROPA_ZUERST:
        ids = europa + [p for p in allgemein if p not in europa]
    else:
        ids = allgemein + [p for p in europa if p not in allgemein]
    if not ids:
        raise RuntimeError("esearch lieferte keine PMIDs")
    print(f"{len(europa)} mit Europa-/Deutschlandbezug, {len(allgemein)} weltweit, "
          f"zusammengefuehrt {len(ids)} Kandidaten "
          f"({'Europa zuerst' if EUROPA_ZUERST else 'weltweit zuerst'}).")
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
    sortpubdate wird ignoriert - PubMed setzt dort bei reinen Monatsangaben
    den 1. ein, was einen Tag vortaeuschen wuerde. Genommen wird die
    genaueste ECHTE Angabe aus pubdate und epubdate.

    **Das Journal gehoert unbedingt hierher, nicht in die Modellantwort.** Beim
    ersten Lauf dieses Portals am 17.08.2026 stand ueber einer Studie aus
    NPJ Prim Care Respir Med der Name Nat Commun, und aus Qual Life Res wurde
    das ausgeschriebene Quality of Life Research. Beides plausibel, beides
    falsch: Der Abstract-Block nennt das Journal nur in der Kopfzeile, und ein
    Sprachmodell ergaenzt dort bereitwillig, was haeufig vorkommt. Eine falsche
    Quellenangabe ist in einem Rechercheportal der teuerste aller kleinen
    Fehler - sie macht die Studie unauffindbar und das Portal unglaubwuerdig.
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
        # wenn sie wirklich da ist - sonst bliebe die Angabe leer, und eine
        # ungefaehre Angabe ist immer noch besser als gar keine.
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
    if len(studies) > ANZAHL_MAX:
        if not KAPPEN:
            raise RuntimeError(f"Unerwartete Studienanzahl: {len(studies)}")
        print(f"{len(studies)} Studien geliefert - auf die ersten {ANZAHL_SOLL} gekuerzt.")
        studies = studies[:ANZAHL_SOLL]
    # Zu wenige dagegen heisst, dass etwas grundsaetzlich schieflief - dann
    # lieber sichtbar scheitern als eine duenne Auswahl veroeffentlichen.
    if len(studies) < ANZAHL_MIN:
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
    # Im Nachtrag traegt der Eintrag den nachgetragenen Tag, nicht den Tag des
    # Laufs. Sonst rutschte ein Bestand von Wochen als "heute aufgenommen" in
    # die Sortierung - und in den offenen Bestand des Newsletters.
    heute = NACHTRAG_TAG or dt.datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")
    neu = 0
    for s in studies:
        if s["pmid"] in known:
            continue
        eintrag = {
            "pmid": s["pmid"], "journal": s["journal"], "year": s["year"],
            "author": s.get("author", ""), "pubdate": s.get("pubdate", ""),
            "added": s.get("added", ""),
            "transfer": s.get("transfer", ""),
            "title": s["title"], "sum": s["sum"], "result": s["result"],
            "aufgenommen": heute,
        }
        if NACHTRAG_TAG:
            # Das Kennzeichen ist keine Formalie: Es haelt den Nachtrag aus dem
            # Newsletter heraus (mailchimp_entwurf.py) und sagt im Archiv
            # ehrlich, dass diese Auswahl spaeter entstanden ist und an jenem
            # Tag nie verschickt wurde.
            eintrag["nachtrag"] = True
        entries.append(eintrag)
        known.add(s["pmid"])
        neu += 1

    entries.sort(key=lambda e: (e["aufgenommen"], e["pmid"]), reverse=True)
    with open(ARCHIVE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    print(f"Archiv: {neu} neu, {len(entries)} gesamt.")
    return len(entries)


def main() -> int:
    global NACHTRAG_TAG
    a = argparse.ArgumentParser(description="Studienauswahl des Tages")
    a.add_argument("--nachtrag", metavar="JJJJ-MM-TT",
                   help="Auswahl fuer einen zurueckliegenden Aufnahmetag nachholen. "
                        "Schreibt NUR ins Archiv - die Seite behaelt ihre aktuelle "
                        "Auswahl, und der Newsletter uebergeht die Eintraege.")
    args = a.parse_args()
    if args.nachtrag:
        try:
            tag = dt.date.fromisoformat(args.nachtrag)
        except ValueError:
            print(f"--nachtrag braucht JJJJ-MM-TT, nicht '{args.nachtrag}'.")
            return 2
        if tag >= dt.datetime.now(ZoneInfo("Europe/Berlin")).date():
            print(f"{tag} liegt nicht in der Vergangenheit - das ist der Regellauf.")
            return 2
        NACHTRAG_TAG = tag.isoformat()
        print(f"Nachtrag fuer den {tag.strftime('%d.%m.%Y')} "
              f"(Aufnahmetag in PubMed, nur Archiv).")

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

    if NACHTRAG_TAG:
        # Kein Eingriff in index.html: Die Startseite zeigt weiter die Auswahl
        # des heutigen Tages. Der Nachtrag fuellt den Ordner, nicht die Bühne.
        update_archive(studies)
        return 0

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
