#!/usr/bin/env python3
"""Alles Themenspezifische der taeglichen Studienauswahl — und sonst nichts.

Beim Anschluss an die Vorlage am 18.08.2026 woertlich aus update_studies.py
uebernommen, damit sich an der taeglichen Auswahl nichts aendert.
`update_studies.py` ist seither in allen Portalen wortgleich und wird zentral
gepflegt; wer die Auswahl aendern will, aendert Text in DIESER Datei.
"""
from __future__ import annotations

import os

# NCBI bittet bei automatisierten Zugriffen um eine Tool-Kennung.
NCBI_TOOL = "klima-gesundheit-portal"

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

# Groesse des Kandidatenpools und Reihenfolge der beiden Abfragen — beides so
# uebernommen, wie dieses Portal es bisher gehandhabt hat. EUROPA_ZUERST=False
# heisst: die allgemeine Abfrage steht vorn. Ein Sprachmodell gewichtet, was es
# zuerst liest; umzustellen ist eine redaktionelle Entscheidung.
POOL_EUROPA = 30
POOL_ALLGEMEIN = 25
EUROPA_ZUERST = True

# Wie viele Studien taeglich erscheinen. KAPPEN=False heisst: zu viele lassen
# den Lauf scheitern, statt gekuerzt zu werden.
# **Nicht ins JSON-Schema schreiben** — die Anthropic-API lehnt minItems > 1
# und maxItems ab.
ANZAHL_SOLL = 6
ANZAHL_MAX = 7
ANZAHL_MIN = 5
KAPPEN = True

# ------------------------------------------------------------------- Prompts
SYSTEM = (
    "Du bist Fachredakteur fuer Klimawandel und Gesundheit / Planetary Health. "
    "Aus einer Liste von PubMed-Abstracts waehlst du die relevantesten aktuellen "
    "Studien aus und fasst sie praezise auf Deutsch zusammen. Deine Leserschaft "
    "arbeitet im deutschen Gesundheitswesen: oeffentlicher Gesundheitsdienst, "
    "Kliniken, Praxen, Kommunen, Gesundheitspolitik."
)

USER_TEMPLATE = """Unten stehen aktuelle PubMed-Abstracts (nach Datum sortiert).

Waehle GENAU 6 Studien aus, die (a) einen erkennbaren Bezug zwischen Umwelt- bzw.
Klimafaktoren und menschlicher Gesundheit haben UND (b) im Abstract ein
BENENNBARES ERGEBNIS berichten. Bei quantitativen Arbeiten heisst das: konkrete
Zahlen (Prozentwerte, relative Risiken, Odds/Hazard Ratios, zurechenbare
Todesfaelle, p-Werte, Fallzahlen) - und die gehoeren dann auch in die
Zusammenfassung.
Qualitative Studien (Interviews, Fokusgruppen) und Expertenpapiere sind
ausdruecklich zugelassen; bei ihnen tritt an die Stelle der Zahl die klar benannte
Kernaussage. Was NICHT genuegt, ist ein Abstract, der nur ankuendigt, was untersucht
wurde, ohne zu sagen, was dabei herauskam.
Ueberspringe Studien ohne Abstract oder ohne benennbares Ergebnis. Achte auf
thematische Vielfalt und mische quantitative und qualitative Arbeiten.

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
