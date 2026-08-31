#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legt bei Mailchimp einen Kampagnen-ENTWURF fuer die Studien des Tages an.

Das Skript baut den fertigen Newsletter, legt ihn als Entwurf an, laesst ihn
vom Torwaechter pruefen und terminiert ihn auf TERMIN_LOKAL. Bis dahin ist er
in Mailchimp mit einem Klick absagbar - das Veto-Fenster ersetzt die Freigabe
von Hand.

Eine E-Mail geht nur noch heraus, wenn der Torwaechter etwas beanstandet: Dann
bekommt die Redaktion die Testausgabe mit dem Grund, und der Entwurf bleibt
liegen. Im Regelfall meldet der taegliche Sammelbericht ueber alle Hubs
(knowledge-hubs/scripts/versand_bericht.py), was terminiert wurde - eine
Meldung statt sechs Vorschauen.

Warum ueberhaupt ein Skript: Mailchimp hat die klassischen Automationen im
Juni 2025 abgeschaltet, darunter die RSS-Kampagne. Der Journey-Builder, der
sie ersetzt, kennt keinen RSS-Ausloeser. Uebrig bleibt die API.

Ablauf:
  1. juengsten Tag aus studien-archiv.json holen; ist er nicht von heute,
     endet das Skript ohne Entwurf - kein Versand ohne neue Studien.
  2. pruefen, ob fuer diesen Tag schon ein Entwurf besteht (doppelte Laeufe).
  3. Kampagne anlegen, Empfaenger ist der Tag "Studien-Newsletter Pubmed".
  4. Inhalt setzen - in der Fassung, die die Leserschaft sieht.
  5. Torwaechter pruefen lassen.
  6. Sauber: terminieren, keine E-Mail. Beanstandet: Inhalt mit Stopp-Kasten
     erneut setzen und als Testausgabe an die Redaktion schicken.

Aufruf:
    python scripts/mailchimp_entwurf.py            # Entwurf anlegen
    python scripts/mailchimp_entwurf.py --probe    # nur HTML nach _probe.html schreiben
    python scripts/mailchimp_entwurf.py --termin 11:00   # einmalig spaeter versenden
    python scripts/mailchimp_entwurf.py --neu      # Entwurf von heute verwerfen
    python scripts/mailchimp_entwurf.py --neu --auch-terminierte
                                                  # ... auch, wenn er schon terminiert ist
                                                   # und neu bauen

--neu ist fuer den Tag gedacht, an dem der Torwaechter morgens gestoppt hat
und die Ursache noch am selben Vormittag behoben wurde. Ohne den Schalter
haelt der liegengebliebene Entwurf den Tag blockiert - sein Titel traegt das
Datum, und Schritt 2 bricht daraufhin ab. Eine bereits **terminierte**
Kampagne wird nie angetastet.

Der Schluessel kommt aus der Umgebung (MAILCHIMP_API_KEY), niemals aus dem
Quelltext. Sein Anhaengsel nach dem Bindestrich ist das Rechenzentrum.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys

import sponsoren
import torwaechter
from html import escape
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_newsletter import load, utm            # gemeinsame Basis, kein zweiter Datenpfad
from thema import ANZAHL_SOLL                    # fuer den Hinweis an duennen Tagen

TZ = ZoneInfo("Europe/Berlin")

LIST_ID = "1c8fc10ec7"          # Zielgruppe "eRelation GESAMT"
# Tag "Studien-Newsletter Klima" (Tags sind statische Segmente). Die Nummer steht
# in der Adresszeile, wenn man den Tag in Mailchimp anklickt. Ohne sie faellt das
# Skript auf die Gruppe zurueck - siehe GRUPPE_NAME.
TAG_ID = 0
# Dieselbe Bestellung kann auch als Gruppe vorliegen: Mailchimps eigene
# Anmeldeseite kann nur Gruppen setzen, keine Tags. Wer sich dort eintraegt,
# traegt nur diese - und bekaeme ohne die zweite Bedingung nie eine Ausgabe.
# Die API kennt Gruppen nicht unter der Nummer aus dem Formular (512), sondern
# unter einer eigenen Kennung; die wird zur Laufzeit ueber den Namen gesucht.
GRUPPE_NAME = "Studien Newsletter Klima"
FROM_NAME = "Monitor Versorgungsforschung"
REPLY_TO = "redaktion@m-vf.de"   # Antworten sollen in der Redaktion landen,
                                # nicht beim Redaktionssystem. Die Adresse muss in
                                # Mailchimp als Absender freigegeben sein.
# Empfaenger der einzigen E-Mail, die dieses Skript noch verschickt: der
# Testausgabe im Stopp-Fall. Im Regelfall meldet der Sammelbericht.
SEITE = "https://klima.m-vf.de"   # einfache Zeichenkette: klima.m-vf.de wird beim
                               # Erzeugen ersetzt, ein f-String wuerde die
                               # Klammern verdoppeln und stehen lassen.
FREIGABE_MAIL = "stegmaier@m-vf.de"

# Wann eine gepruefte Ausgabe rausgeht - **deutsche Ortszeit**, in
# Viertelstundenschritten (Mailchimp nimmt nichts anderes an). Bewusst nicht in
# UTC festgeschrieben: Eine feste UTC-Zeit verschoebe den Versand bei der
# Zeitumstellung um eine Stunde, ohne dass es jemand bemerkt - aus 09:00 wuerde
# im Winter 08:00. naechster_termin() rechnet deshalb von hier nach UTC um.
#
# Der naechtliche Lauf beginnt um 05:00 Ortszeit, die Sammelmeldung kommt um
# 05:45 (beides am 24.08.2026 um eine Stunde vorgezogen). Es bleiben also gut
# vier Stunden, in denen sich die Terminierung mit einem Klick absagen
# laesst. Dieses Fenster ist der ganze Sinn der Sache: Der
# Torwaechter faengt mechanischen Unfug, das Fenster faengt den inhaltlichen.
TERMIN_LOKAL = "10:00"

# Titel aller von hier erzeugten Kampagnen. Daran erkennt das Skript spaeter,
# was schon versendet wurde und was noch aussteht - Mailchimp fuehrt darueber
# selbst kein Buch, seit die RSS-Kampagne weg ist.
#
# **Der Praefix MUSS sich vom Schwesterportal unterscheiden.** Beide Portale
# schreiben in dasselbe Mailchimp-Konto. Am 17.08.2026 lief dieses Skript mit
# dem geerbten Praefix "MVF Studien-Newsletter" und meldete: "Entwurf besteht
# bereits" - es hatte den Entwurf des Versorgungsforschungs-Portals fuer seinen
# eigenen gehalten und legte gar keinen an.
#
# Und er darf mit dem anderen auch nicht *anfangen*: datum_aus_titel() prueft
# mit startswith(), also wuerde "MVF Studien-Newsletter Klima ..." drueben als
# eigene Kampagne durchgehen. Deshalb ein voellig eigener Name. Ein drittes
# Portal braucht wieder einen, der mit keinem der beiden beginnt.
PRAEFIX = "MVF Klima-Newsletter"
# Obergrenze, falls laenger nicht freigegeben wurde. Eine Ausgabe mit 80
# Studien liest niemand; der Rest bleibt im Archiv und im Hub sichtbar.
MAX_STUDIEN = 25
# Tagesbericht fuer den Sammelbericht ueber alle Hubs.
STATUSDATEI = "versand-status.json"

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
          "Juli", "August", "September", "Oktober", "November", "Dezember"]

# Farben des Hubs seit der Umstellung auf das MVF-Erscheinungsbild.
BLAU, GOLD, GOLD_TIEF = "#0051A1", "#BE9E53", "#8A6E28"

# MVF setzt ausschliesslich Lato - auch hier. In E-Mails laesst sich das aber
# nur anbieten, nicht erzwingen: Outlook rendert mit der Word-Engine und kennt
# @font-face nicht, Gmail entfernt es. Apple Mail und iOS laden die Schrift,
# alle anderen fallen auf Helvetica/Arial zurueck - dieselbe humanistische
# Grotesk-Anmutung, keine Serifen. Kein Georgia mehr: Eine Serifenschrift
# widerspricht dem Erscheinungsbild deutlicher als eine Ersatz-Grotesk.
FONT = "'Lato',Helvetica,Arial,sans-serif"
SCHRIFT_EINBINDEN = """<style type="text/css">
@font-face{font-family:'Lato';font-style:normal;font-weight:400;
  src:url('https://klima.m-vf.de/fonts/lato-400.woff2') format('woff2');}
@font-face{font-family:'Lato';font-style:normal;font-weight:700;
  src:url('https://klima.m-vf.de/fonts/lato-700.woff2') format('woff2');}
</style>"""


def lang(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day}. {MONATE[d.month - 1]} {d.year}"


# --------------------------------------------------------------- Newsletter

def studie_html(e: dict) -> str:
    """Eine Studie als Tabellenzeile - Outlook rendert mit der Word-Engine."""
    pubmed = utm(f"https://pubmed.ncbi.nlm.nih.gov/{e['pmid']}/", "email", "studie")
    kopf = "  ·  ".join(x for x in (e.get("author"), e.get("pubdate")) if x)
    titel = escape(e["title"])
    quelle = " · ".join(x for x in (e.get("journal"), str(e.get("year") or "")) if x)
    return f"""
    <tr><td style="padding:22px 28px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="border-top:1px solid #D0D8E4;padding-top:20px;">
          <h2 style="margin:0 0 6px;font:bold 19px/1.35 {FONT};color:{BLAU};">
            <a href="{escape(pubmed)}" style="color:{BLAU};text-decoration:none;">{titel}</a>
          </h2>
          <p style="margin:0 0 10px;font:12px/1.5 {FONT};color:#545C63;">{escape(quelle)}</p>
          {f'<p style="margin:0 0 8px;font:italic 13px {FONT};color:#545C63;">{escape(kopf)}</p>' if kopf else ''}
          <p style="margin:0 0 10px;font:15px/1.65 {FONT};color:#333;">{escape(e["sum"])}</p>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr><td style="background:#F7FAFD;border-left:3px solid {BLAU};padding:10px 14px;
                           font:15px/1.6 {FONT};color:#222;">
              <strong>Ergebnis:</strong> {escape(e["result"])}</td></tr>
          </table>
          {f'<p style="margin:8px 0 0;font:13px/1.6 {FONT};color:#545C63;"><strong>Übertragbarkeit:</strong> {escape(e["transfer"])}</p>' if e.get("transfer") else ''}
          <p style="margin:10px 0 0;font:13px {FONT};">
            <a href="{escape(pubmed)}" style="color:{GOLD_TIEF};">Studie in PubMed ansehen &rarr;</a></p>
        </td></tr>
      </table>
    </td></tr>"""


def tage(studien: list[dict]) -> list[str]:
    """Die vertretenen Aufnahmetage, neueste zuerst."""
    gesehen: list[str] = []
    for e in studien:
        if e["aufgenommen"] not in gesehen:
            gesehen.append(e["aufgenommen"])
    return gesehen


def einleitung(studien: list[dict]) -> str:
    """Sagt, welchen Zeitraum die Ausgabe abdeckt - eine Ausgabe kann mehrere
    Tage nachholen, wenn zwischendurch keine freigegeben wurde."""
    t = tage(studien)
    zeitraum = (f"vom {escape(lang(t[0]))}" if len(t) <= 1
                else f"vom {escape(lang(t[-1]))} bis {escape(lang(t[0]))}")
    return (f"Eine Auswahl aus den PubMed-Neuzugängen {zeitraum}, auf Deutsch "
            f"zusammengefasst und jeweils mit den konkreten Ergebniszahlen versehen.")


def studienteil(studien: list[dict]) -> str:
    """Die Studien, bei mehreren Tagen mit Tagesbalken dazwischen."""
    mehrtaegig = len(tage(studien)) > 1
    teile, letzter = [], None
    for e in studien:
        if mehrtaegig and e["aufgenommen"] != letzter:
            teile.append(tagesbalken(e["aufgenommen"]))
            letzter = e["aufgenommen"]
        teile.append(studie_html(e))
    return "".join(teile)


def tagesbalken(datum: str) -> str:
    """Trennt die Tage, wenn eine Ausgabe mehrere umfasst."""
    return f"""
    <tr><td style="padding:26px 28px 0;">
      <p style="margin:0;font:bold 11px/1.4 {FONT};letter-spacing:1.5px;
                text-transform:uppercase;color:{GOLD_TIEF};">
        Aufgenommen am {escape(lang(datum))}</p>
    </td></tr>"""


def newsletter_html(studien: list[dict], hinweis: str = "",
                    rubriken: str = "") -> str:
    """Die vollstaendige E-Mail.

    `hinweis` erscheint nur in der Testausgabe, `rubriken` nur, wenn sich
    an Ausschreibungen oder News seit der letzten Ausgabe etwas geaendert
    hat (siehe rubriken_html).
    """
    dl = "https://klima.m-vf.de/download"
    p = "utm_source=newsletter&amp;utm_medium=email&amp;utm_campaign=studien-entwurf"
    return f"""{SCHRIFT_EINBINDEN}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#EDF2FA;margin:0;padding:0;">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background:#ffffff;">
    {hinweis}
    <!-- Logo auf weissem Grund: Die Wortmarke ist blau-gold, auf dem blauen
         Kopf ginge der blaue Anteil unter. Viele Programme laden Bilder erst
         auf Klick - deshalb traegt sie einen Alternativtext, und die
         Absenderzeile darunter wiederholt den Namen in Schrift. -->
    <tr><td style="background:#ffffff;padding:18px 28px 14px;">
      <a href="https://klima.m-vf.de/?{p}&amp;utm_content=logo" style="text-decoration:none;">
        <img src="https://klima.m-vf.de/logo/mvf-logo.png" width="170" height="45"
             alt="Monitor Versorgungsforschung"
             style="display:block;border:0;width:170px;height:auto;"></a>
    </td></tr>

    <tr><td style="background:{BLAU};padding:22px 28px;">
      <p style="margin:0;font:bold 12px/1.5 {FONT};letter-spacing:1.5px;color:#C9DCF2;">
        VOM KNOWLEDGE-HUB VON MONITOR VERSORGUNGSFORSCHUNG</p>
      <p style="margin:4px 0 0;font:bold 24px/1.3 {FONT};color:#ffffff;">
        Neueste Studien - Hitze, Klima und Gesundheit</p>
      <p style="margin:6px 0 0;font:13px/1.5 {FONT};color:#D8E5F5;">
        Ausgabe vom {escape(lang(tage(studien)[0]))}</p>
    </td></tr>

    <tr><td style="padding:24px 28px 4px;">
      <p style="margin:0;font:15px/1.65 {FONT};color:#333;">
        {einleitung(studien)} Ausgewählt wird nach Übertragbarkeit auf das
        deutsche Versorgungssystem. Die vollständige Auswahl samt Archiv finden Sie im
        <a href="https://klima.m-vf.de/?{p}&amp;utm_content=intro" style="color:{BLAU};font-weight:bold;">Knowledge-Hub</a>.</p>
    </td></tr>
{studienteil(studien)}
{rubriken}
    <tr><td style="padding:30px 28px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#F7FAFD;border:1px solid #D0D8E4;">
        <tr><td style="padding:18px 22px;">
          <p style="margin:0 0 4px;font:bold 15px/1.4 {FONT};color:{BLAU};">
            Diese Ausgabe herunterladen</p>
          <p style="margin:0 0 12px;font:13px/1.6 {FONT};color:#545C63;">
            Word zum Weiterverarbeiten, Excel zum Auswerten &ndash; jeweils auf dem Stand dieser Ausgabe.</p>
          <p style="margin:0;font:14px/2 {FONT};">
            <a href="{dl}/studien-aktuell.docx?{p}&amp;utm_content=dl-word" style="color:{GOLD_TIEF};font-weight:bold;">&#10515; Word (.docx)</a>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <a href="{dl}/studien-aktuell.csv?{p}&amp;utm_content=dl-excel" style="color:{GOLD_TIEF};font-weight:bold;">&#10515; Excel (.csv)</a></p>
          <p style="margin:12px 0 0;padding-top:12px;border-top:1px solid #D0D8E4;font:13px/1.7 {FONT};color:#545C63;">
            Das <strong>vollständige Archiv</strong> aller bisher vorgestellten Studien:
            <a href="{dl}/studien-archiv.docx?{p}&amp;utm_content=dl-archiv-word" style="color:{GOLD_TIEF};">Word</a> &middot;
            <a href="{dl}/studien-archiv.csv?{p}&amp;utm_content=dl-archiv-excel" style="color:{GOLD_TIEF};">Excel</a></p>
        </td></tr>
      </table>
    </td></tr>

    <tr><td style="padding:26px 28px 0;" align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="background:{GOLD};">
          <a href="https://klima.m-vf.de/?{p}&amp;utm_content=cta"
             style="display:inline-block;padding:13px 28px;font:bold 15px {FONT};color:#2A2207;text-decoration:none;">
            Zum MVF-Knowledge-Hub</a>
        </td></tr>
      </table>
      <p style="margin:12px 0 0;font:13px/1.6 {FONT};color:#545C63;">
        56 Fachdatenbanken, 29 davon mit Direktsuche &ndash; ein Suchbegriff, alle Quellen.</p>
    </td></tr>

    <tr><td style="padding:28px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="border-top:1px solid #D0D8E4;padding-top:18px;font:12px/1.7 {FONT};color:#777;">
          {sponsor_fuss()}
          <p style="margin:0 0 8px;">
            <strong>Monitor Versorgungsforschung</strong><br>
            eRelation AG &ndash; Content in Health<br>
            *|HTML:LIST_ADDRESS_HTML|*</p>
          <p style="margin:0;">
            Sie erhalten diese E-Mail, weil Sie den Studien-Newsletter abonniert haben.<br>
            <a href="*|UNSUB|*" style="color:#777;">Abmelden</a> &middot;
            <a href="*|UPDATE_PROFILE|*" style="color:#777;">Einstellungen ändern</a> &middot;
            <a href="https://klima.m-vf.de/#datenschutz" style="color:#777;">Datenschutz</a></p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</td></tr>
</table>"""


# ------------------------------------------------------ Rubriken (Punkt C)
# Beide erscheinen NUR, wenn sich seit der letzten versendeten Ausgabe etwas
# geaendert hat - Entscheidung des Herausgebers vom 28.08.2026. Ausschreibungen
# aendern sich im Wochen-, nicht im Tagesrhythmus; stuende die Rubrik jeden Tag
# darin, waere sie nach drei Ausgaben unsichtbar. Verglichen wird ueber eine
# Signatur der Adressen, die in versand-status.json mitgeschrieben wird.

# Hoechstens so viele Zeilen je Rubrik. Der Newsletter ist die Studienauswahl;
# die Rubriken sind Beiwerk und duerfen sie nicht ueberwachsen.
RUBRIK_MAX = 3


def signatur(adressen: list[str]) -> str:
    """Ein kurzer Fingerabdruck einer Rubrik - unabhaengig von der Reihenfolge."""
    if not adressen:
        return ""
    roh = "|".join(sorted(adressen))
    return hashlib.sha1(roh.encode("utf-8")).hexdigest()[:16]


def radar_eintraege() -> list[dict]:
    """Die offenen Ausschreibungen des eigenen Themengebiets.

    Geholt ueber `radar_hinweis.gliederung()` - dieselbe Quelle wie die Karte
    auf der Seite, damit Newsletter und Hub nicht auseinanderlaufen. Faellt
    der Abruf aus, entfaellt die Rubrik; sie ist Beiwerk, kein Grund, eine
    Ausgabe zu verzoegern.
    """
    try:
        import radar_hinweis
        daten = radar_hinweis.gliederung()
        slug = json.loads(pathlib.Path("portal.json").read_text(
            encoding="utf-8")).get("SLUG")
    except Exception as fehler:  # noqa: BLE001 - Beiwerk darf ausfallen
        print(f"Radar-Rubrik entfaellt: {fehler}")
        return []
    heute = dt.datetime.now(TZ).date()
    for gebiet in daten.get("themen") or []:
        if gebiet.get("slug") != slug:
            continue
        # Im Browser rechnet die Seite die Fristen nach; hier muss es das
        # Skript tun, sonst steht im Newsletter eine abgelaufene Frist.
        return [e for e in gebiet.get("ausschreibungen") or []
                if (e.get("frist") or "9999") >= heute.isoformat()][:RUBRIK_MAX]
    return []


def news_eintraege() -> list[dict]:
    """Die Beitraege, die scharf vorher `newsfeed.py` in index.html gesetzt hat.

    Aus der Seite gelesen und nicht neu gesucht: Newsletter und Hub sollen
    dieselben Beitraege zeigen, und der Workflow hat den Block Minuten vorher
    geschrieben.
    """
    try:
        seite = pathlib.Path("index.html").read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"const NEWS = \[(.*?)\];", seite, re.S)
    if not m:
        return []
    eintraege = []
    for stueck in re.finditer(
            r'\{titel:"(.*?)", datum:"(.*?)", url:"(.*?)"\}', m.group(1)):
        eintraege.append({"titel": json.loads(f'"{stueck.group(1)}"'),
                          "datum": stueck.group(2), "url": stueck.group(3)})
    return eintraege[:RUBRIK_MAX]


def rubrik_html(ueberschrift: str, unterzeile: str, zeilen: list[str],
                knopf: tuple[str, str] | None = None) -> str:
    """Ein Kasten im Stil des Download-Kastens - oder nichts."""
    if not zeilen:
        return ""
    knopfzeile = ""
    if knopf:
        knopfzeile = (
            f'\n          <p style="margin:12px 0 0;font:14px/1.7 {FONT};">'
            f'<a href="{knopf[1]}" style="color:{GOLD_TIEF};font-weight:bold;">'
            f'{knopf[0]} &#8599;</a></p>')
    return f"""
    <tr><td style="padding:26px 28px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#F7FAFD;border:1px solid #D0D8E4;">
        <tr><td style="padding:18px 22px;">
          <p style="margin:0 0 4px;font:bold 15px/1.4 {FONT};color:{BLAU};">
            {ueberschrift}</p>
          <p style="margin:0 0 12px;font:13px/1.6 {FONT};color:#545C63;">
            {unterzeile}</p>
          {"".join(zeilen)}{knopfzeile}
        </td></tr>
      </table>
    </td></tr>"""


def rubriken_html(radar: list[dict], news: list[dict]) -> str:
    """Beide Rubriken, soweit sie heute etwas Neues zu sagen haben."""
    p = "utm_source=newsletter&amp;utm_medium=email&amp;utm_campaign=rubriken"
    teile = []

    zeilen = []
    for e in radar:
        frist = dt.date.fromisoformat(e["frist"]).strftime("%d.%m.%Y")
        zeilen.append(
            f'<p style="margin:0 0 10px;font:14px/1.5 {FONT};color:#333;">'
            f'<a href="{escape(e["url"])}" style="color:{BLAU};font-weight:bold;">'
            f'{escape(e["titel"])}</a><br>'
            f'<span style="font:12px/1.5 {FONT};color:#545C63;">'
            f'Frist: {frist} &middot; {escape(e.get("quelle", ""))}</span></p>')
    teile.append(rubrik_html(
        "Neue Ausschreibungen zu diesem Themenfeld",
        "Offene F&ouml;rderbekanntmachungen aus Bund, DFG und grants.gov. "
        "Ma&szlig;geblich ist stets die Bekanntmachung selbst.",
        zeilen,
        ("Alle Ausschreibungen im Radar",
         f"https://wissen.m-vf.de/ausschreibungen.html?{p}")))

    zeilen = []
    for e in news:
        datum = ""
        try:
            datum = dt.date.fromisoformat(e["datum"]).strftime("%d.%m.%Y")
        except ValueError:
            pass
        zeilen.append(
            f'<p style="margin:0 0 10px;font:14px/1.5 {FONT};color:#333;">'
            f'<a href="{escape(e["url"])}" style="color:{BLAU};font-weight:bold;">'
            f'{escape(e["titel"])}</a>'
            + (f'<br><span style="font:12px/1.5 {FONT};color:#545C63;">{datum}'
               f'</span>' if datum else "") + "</p>")
    teile.append(rubrik_html(
        "Ausgesuchte News zu diesem Themenfeld",
        "Beitr&auml;ge aus dem Portal von &bdquo;Monitor Versorgungsforschung&ldquo; "
        "(www.m-vf.de).",
        zeilen))

    return "".join(teile)


def sponsor_fuss() -> str:
    """Der Sponsorenhinweis fuer die Fusszeile - leer, wenn es keinen gibt.

    Das Logo braucht hier eine ABSOLUTE Adresse: E-Mail-Programme loesen keine
    relativen Pfade auf. Sie zeigt auf das Portal selbst, nicht auf den Server
    des Sponsors - damit gilt auch im Newsletter, was auf der Seite gilt.
    """
    liste = sponsoren.lade()
    if not liste:
        return ""
    bilder = "".join(
        f'<a href="{escape(s["u"])}" style="text-decoration:none;">'
        f'<img src="{SEITE}/{escape(s["logo"])}" height="34" alt="{escape(s["n"])}"'
        f' style="height:34px;width:auto;border:0;vertical-align:middle;margin-right:14px;"></a>'
        for s in liste)
    return (f'<p style="margin:0 0 14px;">'
            f'<span style="display:block;margin-bottom:6px;">Gesponsert von '
            f'&ndash; ohne Einfluss auf die Inhalte:</span>{bilder}</p>')


# Kein Versand am Wochenende. Die Hubs werden weiter taeglich aktualisiert -
# nur die E-Mail pausiert. Was am Samstag und Sonntag hinzukommt, bleibt offen
# und laeuft montags mit; das Skript versendet ohnehin nicht "die Studien von
# heute", sondern alle noch nicht versendeten.
WOCHENENDE_AUS = True


def montags_hinweis(studien: list[dict]) -> str:
    """Erklaert die laengere Montagsausgabe - sonst wirkt sie wie ein Fehler."""
    return f"""
    <tr><td style="background:#E3EBF7;padding:14px 28px;">
      <p style="margin:0;font:13px/1.6 {FONT};color:#1B3352;">
        <strong>Ausgabe vom Montag.</strong> Sie erhalten heute die Studien vom
        Wochenende und von heute in einer Ausgabe &ndash; deshalb ist diese
        Liste länger als sonst.</p>
    </td></tr>"""


def duenner_tag_hinweis(studien: list[dict]) -> str:
    """Erklaert eine kurze Ausgabe - sonst wirkt sie wie ein Fehler.

    Die Auswahl trifft taeglich SOLL Studien, aber was davon schon einmal im
    Archiv stand, faellt heraus. An ruhigen Tagen bleiben davon ein bis vier
    uebrig. Am 26.08.2026 gingen so neun Ausgaben mit ein bis vier Studien
    hinaus, ohne dass ein Wort dazu darin stand - fuer die Leserschaft sieht
    das nach einer kaputten Ausgabe aus, nicht nach einem ruhigen Tag in
    PubMed.

    Keine Studien heisst weiterhin: kein Newsletter. Das entscheidet der
    Torwaechter ("keine Studien in der Ausgabe"), nicht dieser Kasten.
    """
    n = len(studien)
    if n >= ANZAHL_SOLL:
        return ""
    wieviel = "nur eine Studie" if n == 1 else f"nur {n} Studien"
    return f"""
    <tr><td style="background:#F5F0E4;padding:14px 28px;">
      <p style="margin:0;font:13px/1.6 {FONT};color:#4A3B18;">
        <strong>Heute eine kurze Ausgabe.</strong> Die Ausbeute an neuen
        PubMed-Zugängen war geringer als sonst &ndash; deshalb erhalten Sie
        heute {wieviel}. Morgen früh geht es wie gewohnt weiter.</p>
    </td></tr>"""


def stopp_hinweis(studien: list[dict], link: str, gruende: list[str]) -> str:
    """Steht nur in der Testausgabe nach einer Beanstandung, nie im Versand.

    Der Kasten sagt bewusst, dass NICHTS terminiert ist. Solange er im
    Regelfall mitlief, behauptete er "Entwurf zur Freigabe", obwohl der
    Versand langst auf 10:00 Uhr stand - eine Meldung, die das Gegenteil
    dessen sagte, was geschah.

    **Der erste Satz gilt erst seit dem 24.08.2026.** Die Testausgabe ist
    eine Kopie des Kampagneninhalts; dieser Kasten muss also in die Kampagne
    geschrieben werden, um in der Testmail zu landen. Vorher blieb er danach
    darin stehen - und der Handversand, den der Kasten selbst anbietet,
    haette ihn an die Leserschaft geschickt. Der Aufrufer setzt den Inhalt
    unmittelbar nach dem Testversand wieder zurueck; wer diese Zeile
    entfernt, macht den Satz erneut zur Unwahrheit.
    """
    anzahl = len(studien)
    t = tage(studien)
    zeitraum = (f" vom {lang(t[0])}" if len(t) == 1
                else f" aus {len(t)} Tagen ({lang(t[-1])} bis {lang(t[0])})")
    return f"""
    <tr><td style="background:{GOLD};padding:16px 28px;">
      <p style="margin:0 0 6px;font:bold 15px/1.4 {FONT};color:#2A2207;">
        Gestoppt &ndash; diese Ausgabe wird nicht versendet</p>
      <p style="margin:0;font:13px/1.6 {FONT};color:#2A2207;">
        {anzahl} Studien{zeitraum}. Der Torwächter hat beanstandet:<br>
        <strong>{escape('; '.join(gruende) or 'unbekannt')}</strong><br>
        Der Entwurf liegt in Mailchimp und ist nicht terminiert. Zum Ansehen,
        Bearbeiten und gegebenenfalls Senden von Hand:<br>
        <a href="{escape(link)}" style="color:#2A2207;"><strong>{escape(link)}</strong></a><br>
        Dieser Kasten steht nur in dieser Testausgabe. Der Entwurf in Mailchimp
        enth&auml;lt ihn nicht und kann unver&auml;ndert gesendet werden.</p>
    </td></tr>"""


# ---------------------------------------------------------------- Mailchimp

class Mailchimp:
    def __init__(self, key: str):
        if "-" not in key:
            raise SystemExit("MAILCHIMP_API_KEY ohne Rechenzentrum (erwartet: ...-usX)")
        self.dc = key.rsplit("-", 1)[1]
        self.basis = f"https://{self.dc}.api.mailchimp.com/3.0"
        self.auth = ("anystring", key)

    def _ruf(self, methode: str, pfad: str, **kw):
        r = requests.request(methode, self.basis + pfad, auth=self.auth, timeout=45, **kw)
        if not r.ok:
            # Mailchimps Fehler stecken im Rumpf, nicht im Statustext.
            raise SystemExit(f"Mailchimp {methode} {pfad}: {r.status_code} {r.text[:500]}")
        return r.json() if r.text else {}

    def entwuerfe(self) -> list[dict]:
        """Entwuerfe UND bereits terminierte Kampagnen.

        Mailchimp filtert /campaigns nur nach EINEM Status, deshalb mehrere
        Abrufe. Bis zum 31.08.2026 wurde nur "save" geholt, und das war teuer:
        Eine bereits TERMINIERTE Kampagne war fuer die Doppelpruefung weiter
        unten unsichtbar. Jeder weitere Lauf des Tages legte deshalb eine
        zweite mit demselben Titel an und terminierte sie auf dieselbe Minute.
        Am 31.08.2026 liefen drei Laeufe - GitHub-Cron, Reservecron und der
        neue Weckruf vom MVF-Server - und die Leserschaft bekam jede Ausgabe
        dreimal.

        Versendete Kampagnen fehlen hier bewusst: Die faengt `seit` ab. Nach
        einem Versand steht nichts Offenes mehr an, und das Skript endet
        vorher mit "Nichts offen".
        """
        raus = []
        for stand in ("save", "schedule", "sending"):
            d = self._ruf("GET", "/campaigns", params={
                "status": stand, "count": 50,
                "sort_field": "create_time", "sort_dir": "DESC"})
            raus.extend(d.get("campaigns", []))
        return raus

    def gruppe_suchen(self, name: str) -> tuple[str, str] | None:
        """Kennung von Kategorie und Gruppe zum sichtbaren Namen - oder None."""
        try:
            kats = self._ruf("GET", f"/lists/{LIST_ID}/interest-categories",
                             params={"count": 60}).get("categories", [])
            for k in kats:
                ints = self._ruf("GET", f"/lists/{LIST_ID}/interest-categories/{k['id']}/interests",
                                 params={"count": 60}).get("interests", [])
                for i in ints:
                    if i.get("name", "").strip().lower() == name.strip().lower():
                        return k["id"], i["id"]
        except SystemExit as fehler:
            print(f"Gruppe '{name}' nicht ermittelbar ({fehler}) - nur ueber den Tag.")
        return None

    def anlegen(self, titel: str, betreff: str) -> dict:
        # Empfaenger: Tag ODER Gruppe. "any" ist hier wesentlich - mit "all"
        # bekaeme die Ausgabe nur, wer zufaellig beide Kennzeichen traegt.
        # Solange keine Tag-Nummer hinterlegt ist, darf die Tag-Bedingung nicht
        # mitgeschickt werden - Mailchimp lehnt "static_is 0" ab und der ganze
        # Entwurf scheitert. Dann traegt die Gruppe den Versand allein.
        bedingungen = []
        if TAG_ID:
            bedingungen.append({"condition_type": "StaticSegment", "field": "static_segment",
                                "op": "static_is", "value": TAG_ID})
        gruppe = self.gruppe_suchen(GRUPPE_NAME)
        if gruppe:
            kat, interesse = gruppe
            bedingungen.append({"condition_type": "Interests", "field": f"interests-{kat}",
                                "op": "interestcontains", "value": [interesse]})
            print(f"Empfaenger: {'Tag %d oder ' % TAG_ID if TAG_ID else ''}"
                  f"Gruppe '{GRUPPE_NAME}' ({interesse}).")
        elif TAG_ID:
            print(f"Empfaenger: nur Tag {TAG_ID} - Gruppe '{GRUPPE_NAME}' nicht gefunden.")
        else:
            raise SystemExit(
                f"Kein Empfaenger bestimmbar: TAG_ID ist 0 und die Gruppe "
                f"'{GRUPPE_NAME}' wurde in Mailchimp nicht gefunden. Bitte die "
                f"Tag-Nummer in scripts/mailchimp_entwurf.py eintragen.")

        rumpf = {
            "type": "regular",
            "recipients": {"list_id": LIST_ID,
                           "segment_opts": {"match": "any", "conditions": bedingungen}},
            "settings": {"subject_line": betreff, "title": titel,
                         "from_name": FROM_NAME, "reply_to": REPLY_TO,
                         "to_name": "*|FNAME|*", "auto_footer": False},
        }
        try:
            return self._ruf("POST", "/campaigns", json=rumpf)
        except SystemExit as fehler:
            # Lieber an die Tag-Traeger allein als gar nicht: Wird die
            # Gruppenbedingung abgelehnt, faellt der Empfaenger auf den Tag
            # zurueck. Ein Entwurf ohne Empfaenger waere wertlos.
            # Ohne Tag-Nummer gibt es keinen Rueckfallweg - dann lieber laut
            # scheitern als einen Entwurf ohne Empfaenger anlegen.
            if not gruppe or not TAG_ID or "segment" not in str(fehler).lower():
                raise
            print(f"Gruppenbedingung abgelehnt ({fehler}) - nur ueber den Tag.")
            rumpf["recipients"]["segment_opts"] = {"saved_segment_id": TAG_ID}
            return self._ruf("POST", "/campaigns", json=rumpf)

    def gleichnamige(self, titel: str, ausser: str) -> list[tuple[str, str]]:
        """Kampagnen desselben Titels, die hinausgehen oder schon draussen sind.

        Grundlage der Torwaechter-Pruefung gegen den Doppelversand. Entwuerfe
        bleiben absichtlich aussen vor: Die verschicken nichts, und ein
        liegengebliebener Entwurf desselben Tages ist ein normaler Zustand.
        """
        raus = []
        for stand in ("schedule", "sending", "sent"):
            d = self._ruf("GET", "/campaigns", params={
                "status": stand, "count": 50,
                "sort_field": "create_time", "sort_dir": "DESC"})
            for k in d.get("campaigns", []):
                if (k.get("id") != ausser
                        and k.get("settings", {}).get("title") == titel):
                    raus.append((k["id"], stand))
        return raus

    def gesendet(self) -> list[dict]:
        d = self._ruf("GET", "/campaigns", params={
            "status": "sent", "count": 50, "sort_field": "send_time", "sort_dir": "DESC"})
        return d.get("campaigns", [])

    def loeschen(self, kid: str) -> None:
        self._ruf("DELETE", f"/campaigns/{kid}")

    def inhalt(self, kid: str, html: str) -> None:
        self._ruf("PUT", f"/campaigns/{kid}/content", json={"html": html})

    def testen(self, kid: str, adresse: str) -> None:
        self._ruf("POST", f"/campaigns/{kid}/actions/test",
                  json={"test_emails": [adresse], "send_type": "html"})

    def empfaengerzahl(self, kid: str) -> int:
        """Wie viele die Ausgabe bekaemen. Null heisst: etwas stimmt nicht."""
        d = self._ruf("GET", f"/campaigns/{kid}")
        return int(d.get("recipients", {}).get("recipient_count", 0))

    def listengroesse(self) -> int:
        """Wie viele Menschen insgesamt in der Zielgruppe stehen.

        Nur als Bezugsgroesse: Eine Ausgabe, die fast die ganze Liste
        erreicht, hat ihr Segment verloren - siehe torwaechter.pruefe().
        """
        d = self._ruf("GET", f"/lists/{LIST_ID}")
        return int(d.get("stats", {}).get("member_count", 0))

    def termin_aufheben(self, kid: str) -> None:
        """Hebt den Versandtermin auf. Danach ist die Kampagne wieder Entwurf."""
        self._ruf("POST", f"/campaigns/{kid}/actions/unschedule")

    def terminieren(self, kid: str, zeitpunkt: str) -> None:
        # Mailchimp nimmt nur volle Viertelstunden an und lehnt alles in der
        # Vergangenheit ab. Absagen laesst sich das bis zur letzten Minute -
        # ueber die Oberflaeche oder mit actions/unschedule.
        self._ruf("POST", f"/campaigns/{kid}/actions/schedule",
                  json={"schedule_time": zeitpunkt})


# --------------------------------------------------------------------- main

def naechster_termin(uhrzeit: str | None = None) -> str:
    """Der naechste TERMIN_LOKAL, der noch in der Zukunft liegt - als UTC-ISO.

    Gerechnet wird in Europe/Berlin und erst am Ende nach UTC gewandelt, damit
    der Versand sommers wie winters um dieselbe Ortszeit stattfindet.

    `uhrzeit` weicht einmalig ab - fuer den Tag, an dem der Lauf spaeter kam als
    sonst. Am 27.08.2026 hat GitHub die naechtlichen Laeufe verschluckt; die
    Ausgabe war erst gegen 11:00 fertig, und statt sie auf den naechsten Morgen
    zu schieben (wo sie mit der regulaeren Ausgabe kollidiert waere), ging sie
    noch am selben Tag um 11:00 hinaus. Mailchimp nimmt nur volle
    Viertelstunden.
    """
    jetzt = dt.datetime.now(TZ)
    stunde, minute = (int(x) for x in (uhrzeit or TERMIN_LOKAL).split(":"))
    ziel = jetzt.replace(hour=stunde, minute=minute, second=0, microsecond=0)
    if ziel <= jetzt + dt.timedelta(minutes=20):
        # Zu knapp oder schon vorbei: dann morgen. Ein Termin in zehn Minuten
        # waere kein Veto-Fenster, sondern nur eine Verzoegerung.
        ziel += dt.timedelta(days=1)
    if WOCHENENDE_AUS:
        # Ein Freitagslauf nach 10:00 wuerde sonst auf Samstag rutschen.
        while ziel.weekday() >= 5:
            ziel += dt.timedelta(days=1)
    return ziel.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def poolzahlen() -> dict:
    """Die Zahlen des heutigen Studienlaufs, falls er im selben Workflow lief.

    Fehlt die Datei oder ist sie von gestern, bleibt das Feld leer - beim
    Neubau von Hand (--neu) lief kein Studienlauf, und eine alte Zahl waere
    schlimmer als keine.
    """
    try:
        with open("pool-status.json", encoding="utf-8") as f:
            z = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if z.get("datum") != dt.datetime.now(TZ).date().isoformat():
        return {}
    return {k: z[k] for k in ("geholt", "bekannt", "pool", "gewaehlt", "neu") if k in z}


def alte_signaturen() -> dict:
    """Die Rubrik-Signaturen der letzten Ausgabe - oder ein leeres Paar.

    Sie stehen in versand-status.json, weil die Datei ohnehin bei jedem Lauf
    geschrieben und mitcommittet wird. Eine eigene Datei waere eine weitere,
    die jemand vergessen kann.
    """
    try:
        with open(STATUSDATEI, encoding="utf-8") as f:
            return (json.load(f).get("rubriken") or {})
    except (OSError, ValueError):
        return {}


def schreibe_status(stand: str, titel: str, betreff: str, studien: list[dict],
                    link: str, beanstandungen: list[str], termin: str | None,
                    empfaenger: int = 0, listengroesse: int = 0,
                    aussortiert: list[str] | None = None,
                    rubriken: dict | None = None) -> None:
    """Was heute geschah - fuer den Sammelbericht ueber alle Hubs.

    Die Datei wird vom Workflow mitcommittet; der Bericht im Repo
    knowledge-hubs liest sie von allen Portalen ein und macht daraus EINE
    Meldung statt fuenf.
    """
    with open(STATUSDATEI, "w", encoding="utf-8") as f:
        json.dump({
            "hub": "Knowledge-Hub Hitze, Klima & Gesundheit",
            "domain": "klima.m-vf.de",
            "datum": dt.datetime.now(TZ).date().isoformat(),
            "stand": stand,                      # terminiert | gestoppt
            "titel": titel,
            "betreff": betreff,
            "anzahl": len(studien),
            "pmids": [str(e.get("pmid", "")) for e in studien],
            "kampagne": link,
            "termin_utc": termin,
            "empfaenger": empfaenger,
            "listengroesse": listengroesse,
            "beanstandungen": beanstandungen,
            # Was die Vorpruefung aussortiert hat. Steht hier, damit es nicht
            # still passiert: Der Sammelbericht zeigt es, und nur so faellt
            # auf, wenn taeglich eine Studie durchrutscht.
            "aussortiert": aussortiert or [],
            # Was zwischen PubMed und Ausgabe verlorenging: geholt, davon schon
            # bekannt, Poolgroesse, gewaehlt, davon neu im Archiv. Leer, wenn
            # dieser Lauf keinen Studienlauf hatte.
            "pool": poolzahlen(),
            # Fingerabdruecke der zuletzt AUSGESPIELTEN Rubriken. Sie werden
            # nur fortgeschrieben, wenn die Rubrik heute wirklich in der
            # Ausgabe stand - sonst zeigte der naechste Lauf sie nie wieder.
            "rubriken": rubriken or {},
        }, f, ensure_ascii=False, indent=1)


def datum_aus_titel(titel: str) -> str | None:
    """'MVF Studien-Newsletter 16.08.2026' -> '2026-08-16'."""
    if not titel.startswith(PRAEFIX):
        return None
    rest = titel[len(PRAEFIX):].strip()
    try:
        return dt.datetime.strptime(rest, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def main() -> int:
    probe = "--probe" in sys.argv
    neu = "--neu" in sys.argv
    # Nur zusammen mit --neu und nur von Hand: hebt den Termin einer bereits
    # terminierten Kampagne auf, statt sie stehen zu lassen.
    auch_terminierte = "--auch-terminierte" in sys.argv
    # --termin 11:00 verschiebt NUR diesen einen Versand; TERMIN_LOKAL bleibt.
    termin_arg = None
    if "--termin" in sys.argv:
        termin_arg = sys.argv[sys.argv.index("--termin") + 1]
        if len(termin_arg.split(":")) != 2 or int(termin_arg.split(":")[1]) % 15:
            raise SystemExit(f"--termin {termin_arg}: Mailchimp nimmt nur volle "
                             f"Viertelstunden, Form HH:MM.")
    alle = load()                       # neueste zuerst
    heute = dt.datetime.now(TZ).date().isoformat()

    if probe:
        # Fuer die Vorschau die letzten drei Tage nehmen - so laesst sich auch
        # die mehrtaegige Fassung mit Tagesbalken ansehen.
        drei = tage(alle)[:3]
        studien = [e for e in alle if e["aufgenommen"] in drei][:MAX_STUDIEN]
        # Die Probe zeigt beide Rubriken immer - ungeachtet der Signaturen.
        # Wer die Vorschau aufruft, will sehen, wie die Ausgabe aussieht, wenn
        # es etwas zu melden gibt, nicht ob heute zufaellig etwas neu ist.
        html = newsletter_html(studien, stopp_hinweis(
            studien, "https://beispiel.invalid/entwurf",
            ["Beispielbeanstandung - so sieht der Stopp-Fall aus"]),
            rubriken_html(radar_eintraege(), news_eintraege()))
        with open("_probe.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"{len(studien)} Studien aus {len(tage(studien))} Tagen -> _probe.html")
        return 0

    key = os.environ.get("MAILCHIMP_API_KEY", "").strip()
    if not key:
        print("MAILCHIMP_API_KEY nicht gesetzt - kein Entwurf angelegt.")
        return 0
    mc = Mailchimp(key)

    # Bis wohin ist die Leserschaft bedient? Massgeblich ist der letzte
    # tatsaechlich VERSENDETE Entwurf - nicht der letzte angelegte. Wer eine
    # Ausgabe liegen laesst, soll ihre Studien in der naechsten wiederfinden.
    versendet = [d for d in (datum_aus_titel(k.get("settings", {}).get("title", ""))
                             for k in mc.gesendet()) if d]
    seit = max(versendet) if versendet else None

    # Nachgetragene Eintraege bleiben aussen vor. Sie stammen aus einem
    # spaeteren Lauf ueber zurueckliegende Aufnahmetage; sie gehoeren in den
    # Ordner, aber in keine Ausgabe - sonst bekaeme die Leserschaft nach einem
    # Nachtrag einmalig den gesamten Bestand von Wochen in einer E-Mail.
    alle = [e for e in alle if not e.get("nachtrag")]
    offen = [e for e in alle if seit is None or e["aufgenommen"] > seit]
    if not offen:
        print(f"Nichts offen - zuletzt versendet wurde der Stand vom {seit}.")
        return 0

    if WOCHENENDE_AUS and dt.date.fromisoformat(heute).weekday() >= 5:
        # Kein Entwurf, keine Terminierung - die offenen Studien laufen am
        # Montag mit. Die Seite selbst ist da laengst aktualisiert.
        tag = "Samstag" if dt.date.fromisoformat(heute).weekday() == 5 else "Sonntag"
        print(f"{tag}: kein Versand. {len(offen)} offene Studien warten auf Montag.")
        return 0

    entwuerfe = [(k, datum_aus_titel(k.get("settings", {}).get("title", "")))
                 for k in mc.entwuerfe()]
    eigene = [(k, d) for k, d in entwuerfe if d]
    neu_heute = any(e["aufgenommen"] == heute for e in offen)

    if not neu_heute and eigene and not neu:
        # Nichts Neues, und der offene Bestand liegt bereits als Entwurf bereit.
        # --neu geht auch hier weiter: Wer den Neubau ausdruecklich anfordert,
        # will ihn gerade dann, wenn schon ein Entwurf liegt.
        print(f"Keine neuen Studien heute; {len(offen)} offene liegen im Entwurf "
              f"'{eigene[0][0]['settings']['title']}'.")
        return 0

    # Erste Stufe des Torwaechters: einzelne missglueckte Studien fallen hier
    # heraus, bevor die Ausgabe gebaut wird. Muss VOR dem HTML-Bau stehen -
    # der Torwaechter prueft spaeter, dass jede Studie auch im HTML vorkommt.
    offen, aussortiert = torwaechter.vorpruefung(offen)
    for x in aussortiert:
        print("  ~ aussortiert: " + x)
    if aussortiert:
        print(f"{len(aussortiert)} Studie(n) aussortiert, {len(offen)} bleiben.")

    if len(offen) > MAX_STUDIEN:
        print(f"{len(offen)} offene Studien - auf die {MAX_STUDIEN} neuesten begrenzt.")
        offen = offen[:MAX_STUDIEN]

    titel = f"{PRAEFIX} {dt.date.fromisoformat(heute).strftime('%d.%m.%Y')}"
    t = tage(offen)
    betreff = ("Neueste Studien - Hitze, Klima und Gesundheit – " +
               (lang(t[0]) if len(t) == 1 else f"{lang(t[-1])} bis {lang(t[0])}"))

    for k, d in eigene:
        if k["settings"]["title"] == titel:
            # Mit --neu wird der bestehende Entwurf desselben Tages verworfen
            # und die Ausgabe neu gebaut. Gedacht fuer den Fall, dass der
            # Torwaechter morgens gestoppt hat und die Ursache am selben Tag
            # behoben wurde: Ohne das haelt der liegengebliebene Entwurf den
            # Tag blockiert, denn der Titel traegt das Datum. Ein terminierter
            # Entwurf wird nicht angetastet - der geht ohnehin raus.
            # Eine bereits TERMINIERTE Kampagne bleibt tabu - sie geht ohnehin
            # raus, und sie versehentlich zu verwerfen hiesse, den Versand des
            # Tages zu verlieren. Wer sie ausdruecklich ersetzen will, weil der
            # Inhalt vor 10:00 noch falsch ist, sagt es mit
            # --auch-terminierte; dann wird der Termin aufgehoben und die
            # Ausgabe neu gebaut und neu terminiert.
            if neu and k.get("status") == "schedule" and auch_terminierte:
                mc.termin_aufheben(k["id"])
                mc.loeschen(k["id"])
                print(f"TERMINIERTE Kampagne '{titel}' ({k['id']}) aufgehoben "
                      f"und verworfen - wird neu gebaut.")
                continue
            if neu and k.get("status") == "save":
                mc.loeschen(k["id"])
                print(f"Bestehenden Entwurf '{titel}' ({k['id']}) verworfen "
                      f"- wird neu gebaut.")
                continue
            print(f"Entwurf '{titel}' besteht bereits ({k['id']}) - nichts zu tun.")
            return 0

    kampagne = mc.anlegen(titel, betreff)
    kid = kampagne["id"]
    link = f"https://{mc.dc}.admin.mailchimp.com/campaigns/edit?id={kampagne.get('web_id', '')}"

    # Gleich die Fassung, die die Leserschaft sieht. Frueher lief hier erst
    # eine Testausgabe mit Freigabe-Kasten mit; die ist entfallen, seit der
    # Torwaechter terminiert und der Sammelbericht taeglich meldet.
    # Montags erklaert ein Kasten die laengere Ausgabe. Nur dann, und nur
    # wenn wirklich mehrere Tage darin stecken - sonst erklaert er nichts.
    montag = (WOCHENENDE_AUS and dt.date.fromisoformat(heute).weekday() == 0
              and len(t) > 1)
    hinweise = (montags_hinweis(offen) if montag else "") + duenner_tag_hinweis(offen)

    # ---- Rubriken: nur, wenn sich etwas geaendert hat ----------------------
    # Der Vergleich laeuft gegen die letzte Ausgabe, nicht gegen gestern: Wurde
    # gestern gestoppt, stand die Rubrik nicht drin, und sie soll heute
    # erscheinen. Deshalb werden die Signaturen weiter unten nur bei
    # tatsaechlich ausgespielten Rubriken fortgeschrieben.
    vorher = alte_signaturen()
    radar = radar_eintraege()
    news = news_eintraege()
    sig_radar = signatur([e["url"] for e in radar])
    sig_news = signatur([e["url"] for e in news])
    zeige_radar = bool(radar) and sig_radar != vorher.get("radar")
    zeige_news = bool(news) and sig_news != vorher.get("news")
    for name, zeigen, wieviel in (("Ausschreibungen", zeige_radar, len(radar)),
                                  ("News", zeige_news, len(news))):
        print(f"Rubrik {name}: "
              + (f"{wieviel} Eintrag/Eintraege" if zeigen
                 else "unveraendert seit der letzten Ausgabe - entfaellt"))
    rubriken = rubriken_html(radar if zeige_radar else [],
                             news if zeige_news else [])
    # Fortgeschrieben wird nur, was heute auch wirklich in der Ausgabe stand.
    signaturen = dict(vorher)
    if zeige_radar:
        signaturen["radar"] = sig_radar
    if zeige_news:
        signaturen["news"] = sig_news

    sauber = newsletter_html(offen, hinweise, rubriken)
    mc.inhalt(kid, sauber)

    # ------------------------------------------------------------ Torwaechter
    # Ab hier entscheidet die Maschine, ob die Ausgabe rausgeht. Schlaegt auch
    # nur eine Pruefung an, wird NICHT terminiert: Der Entwurf bleibt liegen,
    # der Grund steht in versand-status.json, und die Redaktion bekommt die
    # einzige E-Mail, die dieses Skript noch verschickt - die Testausgabe mit
    # dem Stopp-Kasten. Dann eben doch von Hand.
    empfaenger = mc.empfaengerzahl(kid)
    gesamt = mc.listengroesse()
    print(f"Empfaengerzahl: {empfaenger} von {gesamt} in der Zielgruppe.")
    # Zweite Linie gegen den Doppelversand: Was zaehlt, ist nicht die Absicht
    # dieses Laufs, sondern was bei Mailchimp fuer heute schon bereitliegt.
    doppelt = mc.gleichnamige(titel, kid)
    if doppelt:
        print(f"Gleichnamige Kampagne(n) fuer heute: "
              f"{', '.join(k for k, _ in doppelt)}")
    beanstandungen = torwaechter.pruefe(
        offen, html=sauber, empfaenger=empfaenger, listengroesse=gesamt,
        gleichnamige=doppelt)
    termin = naechster_termin(termin_arg)
    if beanstandungen:
        print(f"Torwaechter: {len(beanstandungen)} Beanstandung(en) - nicht terminiert.")
        for x in beanstandungen:
            print("  ! " + x)
        mc.inhalt(kid, newsletter_html(
            offen, stopp_hinweis(offen, link, beanstandungen) + hinweise,
            rubriken))
        try:
            mc.testen(kid, FREIGABE_MAIL)
            print(f"Testausgabe mit Stopp-Kasten an {FREIGABE_MAIL} verschickt.")
        except SystemExit as fehler:
            # Kein Grund abzubrechen: Die Meldung steht ohnehin im
            # Sammelbericht, und der Kasten muss trotzdem wieder heraus.
            print(f"Testausgabe konnte nicht verschickt werden: {fehler}")

        # Und den Kasten in jedem Fall wieder aus der Kampagne nehmen.
        #
        # Bis zum 24.08.2026 blieb er darin stehen. Der Kasten behauptete
        # "Dieser Kasten steht nur in dieser Testausgabe; die Leserschaft
        # sieht ihn nicht" - und bot im selben Atemzug an, den Entwurf von
        # Hand zu senden. Am 24.08.2026 ist genau das passiert: Vier
        # Abonnenten des Pflege-Hubs bekamen "Gestoppt - diese Ausgabe wird
        # nicht versendet", die Beanstandung im Klartext und einen internen
        # Mailchimp-Bearbeitungslink. Die Testausgabe ist eben eine Kopie des
        # Kampagneninhalts, kein eigenes Dokument.
        #
        # Scheitert das Zurücksetzen, darf das NICHT still bleiben - dann
        # liegt in Mailchimp wieder ein Entwurf, dessen Handversand die
        # interne Notiz mitnimmt. Die Warnung geht deshalb in die
        # Beanstandungen und damit in den Sammelbericht.
        try:
            mc.inhalt(kid, sauber)
            print("Entwurf auf die saubere Fassung zurueckgesetzt - "
                  "er kann unveraendert von Hand gesendet werden.")
        except SystemExit as fehler:
            print(f"WARNUNG: Entwurf nicht zurueckgesetzt ({fehler})")
            beanstandungen = beanstandungen + [
                "ACHTUNG: Der Entwurf traegt noch den internen Stopp-Kasten "
                "- nicht von Hand senden, ohne ihn vorher zu entfernen"]

        # Bei einer gestoppten Ausgabe bleiben die alten Signaturen stehen:
        # Versendet wurde nichts, also hat die Leserschaft die Rubrik nicht
        # gesehen, und morgen soll sie es noch einmal versuchen.
        schreibe_status("gestoppt", titel, betreff, offen, link, beanstandungen,
                        None, empfaenger, gesamt, aussortiert, vorher)
    else:
        mc.terminieren(kid, termin)
        print(f"Torwaechter: nichts zu beanstanden - terminiert auf {termin}.")
        schreibe_status("terminiert", titel, betreff, offen, link, [], termin,
                        empfaenger, gesamt, aussortiert, signaturen)

    # Aeltere, nie versendete Entwuerfe sind jetzt ueberholt: Ihre Studien
    # stecken vollstaendig im neuen. Zwei Entwuerfe mit ueberlappendem Inhalt
    # waeren eine Falle - man gibt beide frei und verschickt doppelt.
    for k, d in eigene:
        if d and d < heute:
            mc.loeschen(k["id"])
            print(f"Ueberholten Entwurf geloescht: {k['settings']['title']}")

    print(f"Entwurf angelegt: {titel} - {len(offen)} Studien aus {len(t)} Tag(en)")
    if len(t) > 1:
        print(f"  darunter Nachzuegler seit {t[-1]} (zuletzt versendet: {seit or 'noch nie'})")
    print(f"Kampagne: {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
