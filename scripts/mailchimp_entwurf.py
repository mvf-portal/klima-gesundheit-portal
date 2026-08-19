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

Der Schluessel kommt aus der Umgebung (MAILCHIMP_API_KEY), niemals aus dem
Quelltext. Sein Anhaengsel nach dem Bindestrich ist das Rechenzentrum.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

import sponsoren
import torwaechter
from html import escape
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_newsletter import load, utm            # gemeinsame Basis, kein zweiter Datenpfad

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
# Der naechtliche Lauf beginnt um 06:00 Ortszeit, die Sammelmeldung kommt um
# 06:45. Es bleiben also gut drei Stunden, in denen sich die Terminierung mit
# einem Klick absagen laesst. Dieses Fenster ist der ganze Sinn der Sache: Der
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


def newsletter_html(studien: list[dict], hinweis: str = "") -> str:
    """Die vollstaendige E-Mail. `hinweis` erscheint nur in der Testausgabe."""
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


def stopp_hinweis(studien: list[dict], link: str, gruende: list[str]) -> str:
    """Steht nur in der Testausgabe nach einer Beanstandung, nie im Versand.

    Der Kasten sagt bewusst, dass NICHTS terminiert ist. Solange er im
    Regelfall mitlief, behauptete er "Entwurf zur Freigabe", obwohl der
    Versand langst auf 10:00 Uhr stand - eine Meldung, die das Gegenteil
    dessen sagte, was geschah.
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
        Dieser Kasten steht nur in dieser Testausgabe; die Leserschaft sieht ihn nicht.</p>
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
        d = self._ruf("GET", "/campaigns", params={
            "status": "save", "count": 50, "sort_field": "create_time", "sort_dir": "DESC"})
        return d.get("campaigns", [])

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

    def terminieren(self, kid: str, zeitpunkt: str) -> None:
        # Mailchimp nimmt nur volle Viertelstunden an und lehnt alles in der
        # Vergangenheit ab. Absagen laesst sich das bis zur letzten Minute -
        # ueber die Oberflaeche oder mit actions/unschedule.
        self._ruf("POST", f"/campaigns/{kid}/actions/schedule",
                  json={"schedule_time": zeitpunkt})


# --------------------------------------------------------------------- main

def naechster_termin() -> str:
    """Der naechste TERMIN_LOKAL, der noch in der Zukunft liegt - als UTC-ISO.

    Gerechnet wird in Europe/Berlin und erst am Ende nach UTC gewandelt, damit
    der Versand sommers wie winters um dieselbe Ortszeit stattfindet.
    """
    jetzt = dt.datetime.now(TZ)
    stunde, minute = (int(x) for x in TERMIN_LOKAL.split(":"))
    ziel = jetzt.replace(hour=stunde, minute=minute, second=0, microsecond=0)
    if ziel <= jetzt + dt.timedelta(minutes=20):
        # Zu knapp oder schon vorbei: dann morgen. Ein Termin in zehn Minuten
        # waere kein Veto-Fenster, sondern nur eine Verzoegerung.
        ziel += dt.timedelta(days=1)
    return ziel.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def schreibe_status(stand: str, titel: str, betreff: str, studien: list[dict],
                    link: str, beanstandungen: list[str], termin: str | None) -> None:
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
            "beanstandungen": beanstandungen,
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
    alle = load()                       # neueste zuerst
    heute = dt.datetime.now(TZ).date().isoformat()

    if probe:
        # Fuer die Vorschau die letzten drei Tage nehmen - so laesst sich auch
        # die mehrtaegige Fassung mit Tagesbalken ansehen.
        drei = tage(alle)[:3]
        studien = [e for e in alle if e["aufgenommen"] in drei][:MAX_STUDIEN]
        html = newsletter_html(studien, stopp_hinweis(
            studien, "https://beispiel.invalid/entwurf",
            ["Beispielbeanstandung - so sieht der Stopp-Fall aus"]))
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

    offen = [e for e in alle if seit is None or e["aufgenommen"] > seit]
    if not offen:
        print(f"Nichts offen - zuletzt versendet wurde der Stand vom {seit}.")
        return 0

    entwuerfe = [(k, datum_aus_titel(k.get("settings", {}).get("title", "")))
                 for k in mc.entwuerfe()]
    eigene = [(k, d) for k, d in entwuerfe if d]
    neu_heute = any(e["aufgenommen"] == heute for e in offen)

    if not neu_heute and eigene:
        # Nichts Neues, und der offene Bestand liegt bereits als Entwurf bereit.
        print(f"Keine neuen Studien heute; {len(offen)} offene liegen im Entwurf "
              f"'{eigene[0][0]['settings']['title']}'.")
        return 0

    if len(offen) > MAX_STUDIEN:
        print(f"{len(offen)} offene Studien - auf die {MAX_STUDIEN} neuesten begrenzt.")
        offen = offen[:MAX_STUDIEN]

    titel = f"{PRAEFIX} {dt.date.fromisoformat(heute).strftime('%d.%m.%Y')}"
    t = tage(offen)
    betreff = ("Neueste Studien - Hitze, Klima und Gesundheit – " +
               (lang(t[0]) if len(t) == 1 else f"{lang(t[-1])} bis {lang(t[0])}"))

    for k, d in eigene:
        if k["settings"]["title"] == titel:
            print(f"Entwurf '{titel}' besteht bereits ({k['id']}) - nichts zu tun.")
            return 0

    kampagne = mc.anlegen(titel, betreff)
    kid = kampagne["id"]
    link = f"https://{mc.dc}.admin.mailchimp.com/campaigns/edit?id={kampagne.get('web_id', '')}"

    # Gleich die Fassung, die die Leserschaft sieht. Frueher lief hier erst
    # eine Testausgabe mit Freigabe-Kasten mit; die ist entfallen, seit der
    # Torwaechter terminiert und der Sammelbericht taeglich meldet.
    sauber = newsletter_html(offen)
    mc.inhalt(kid, sauber)

    # ------------------------------------------------------------ Torwaechter
    # Ab hier entscheidet die Maschine, ob die Ausgabe rausgeht. Schlaegt auch
    # nur eine Pruefung an, wird NICHT terminiert: Der Entwurf bleibt liegen,
    # der Grund steht in versand-status.json, und die Redaktion bekommt die
    # einzige E-Mail, die dieses Skript noch verschickt - die Testausgabe mit
    # dem Stopp-Kasten. Dann eben doch von Hand.
    beanstandungen = torwaechter.pruefe(
        offen, html=sauber, empfaenger=mc.empfaengerzahl(kid))
    termin = naechster_termin()
    if beanstandungen:
        print(f"Torwaechter: {len(beanstandungen)} Beanstandung(en) - nicht terminiert.")
        for x in beanstandungen:
            print("  ! " + x)
        mc.inhalt(kid, newsletter_html(offen, stopp_hinweis(offen, link, beanstandungen)))
        mc.testen(kid, FREIGABE_MAIL)
        print(f"Testausgabe mit Stopp-Kasten an {FREIGABE_MAIL} verschickt.")
        schreibe_status("gestoppt", titel, betreff, offen, link, beanstandungen, None)
    else:
        mc.terminieren(kid, termin)
        print(f"Torwaechter: nichts zu beanstanden - terminiert auf {termin}.")
        schreibe_status("terminiert", titel, betreff, offen, link, [], termin)

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
