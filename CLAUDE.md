# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projekt

„Knowledge-Hub Hitze, Klima & Gesundheit" — ein Rechercheportal zum Themenfeld Hitze, Klimawandel und Gesundheit. Ein Angebot von **Monitor Versorgungsforschung** (Betreiber: eRelation AG – Content in Health, Bonn).

Live: https://klima.m-vf.de/

**Schwesterportal:** https://wissen.m-vf.de/ (Repo `mvf-portal/versorgungsforschung-portal`) — technisch identisch aufgebaut, anderes Thema. Wer hier etwas an der Mechanik ändert, sollte prüfen, ob es dort ebenso gilt; umgekehrt genauso. Die beiden Portale sind bewusst **getrennte Repositories**, weil GitHub Pages nur eine CNAME-Datei je Repo zulässt.

Projektsprache ist **Deutsch** — Oberfläche, Inhalte, Commit-Messages und Code-Kommentare.

## Kein Build, kein Test, kein Framework

`index.html` ist eine vollständig eigenständige Datei (CSS + HTML + JS inline). Es gibt **kein** npm/package.json, keinen Build-Schritt, keinen Linter und keine Testsuite — entsprechend gibt es auch keine Build-/Test-Kommandos.

| Aufgabe | Vorgehen |
|---|---|
| Lokal ansehen | `index.html` direkt im Browser öffnen (kein Server nötig) |
| Deployen | Commit auf `main` pushen — GitHub Pages baut automatisch (~1 Min) |
| Live prüfen | `curl -s "https://klima.m-vf.de/?cb=$(date +%s)"` — Cache-Buster nötig, sonst kommt die alte Fassung |
| Pages-Build-Status | `gh api repos/mvf-portal/klima-gesundheit-portal/pages/builds/latest` |

`gh` liegt unter `C:\Program Files\GitHub CLI\gh.exe` (nicht im PATH) und ist als `mvf-portal` angemeldet; Scopes: `repo`, `workflow`, `gist`, `read:org`.

Die Seite läuft unter der eigenen Domain **`klima.m-vf.de`** (CNAME-Datei im Repo-Wurzelverzeichnis, HTTPS erzwungen).

## Architektur: datengetriebenes Rendering

Die Seite hat praktisch kein statisches Markup im Body — HTML-Shell plus vier JS-Konstanten, aus denen alles per DOM-Aufbau erzeugt wird:

| Konstante | Erzeugt | Wichtig |
|---|---|---|
| `CATS` | Kategorie-Sektionen + Sprungnavigation | Array-Reihenfolge = Anzeigereihenfolge. `h` ist ein **HSL-Farbton** (0–360), der als CSS-Variable `--h` die Akzentfarbe der Kategorie setzt. `num` ist nur Anzeigetext und muss bei Umsortierung mitgepflegt werden. Das optionale Feld **`hinweis`** setzt einen erklärenden Absatz über die Kacheln — nur die Rubrik `klimadaten` trägt ihn. |
| `DB` | Datenbank-Kacheln | `c` verweist auf `CATS[].id`; die Reihenfolge innerhalb einer Kategorie folgt der Array-Reihenfolge (`filter` erhält sie). |
| `STUDIES` + `SNAP_DATE` | Studien-Frame rechts | Liegt im Marker-Block (siehe unten) und wird maschinell ersetzt. |
| `CHIPS` | Schnellwahl-Buttons unter dem Suchfeld | Reine Strings; setzen das Suchfeld und lösen `apply()` aus. |

### Das `%s`-Mechanismus (Kern der Anwendung)

Jeder `DB`-Eintrag hat einen Typ `t`, der Badge **und** Link-Verhalten steuert:

- **`live`** — `u` enthält `%s`. `apply()` ersetzt `%s` bei jeder Eingabe durch den URL-kodierten Suchbegriff und schreibt den `href` aller Kacheln neu. Ohne Suchbegriff fällt der Link auf die Basis-URL zurück.
- **`portal`** — feste URL (Anbieter ohne Deeplink-Suche, z. B. LIVIVO, DRKS).
- **`lic`** — feste URL, kostenpflichtig/lizenziert (Scopus, Web of Science …).

Eine neue Datenbank mit Suchunterstützung aufzunehmen heißt also: einen `DB`-Eintrag mit `t:"live"` und `%s` in der URL anlegen — die Verdrahtung passiert automatisch über `cardIndex` und `apply()`.

### Marker-Block — die einzige maschinell editierte Stelle

```js
// === STUDIES-BLOCK-START (taeglich 06:00 Uhr von GitHub Actions ersetzt) ===
const SNAP_DATE = "…";
const STUDIES = [ … ];
// === STUDIES-BLOCK-ENDE ===
```

Studien-Updates ersetzen **ausschließlich** diesen Bereich (beide Marker-Zeilen bleiben stehen). Alles andere — CSS, `DB`, `CATS`, Footer, Impressum — bleibt unangetastet. `SNAP_DATE` erscheint sichtbar als „Zuletzt aktualisiert" und muss bei jedem Update auf den aktuellen Zeitpunkt gesetzt werden (Format `"TT. Mon. JJJJ, HH:MM Uhr"`, deutsche Monatsabkürzung).

### Archiv „Ältere Suchergebnisse"

`studien-archiv.json` im Wurzelverzeichnis ist die **vollständige Historie** aller je gezeigten Studien — ein flaches Array mit `pmid`, `journal`, `year`, `title`, `sum`, `result` und `aufgenommen` (ISO-Datum der ersten Sichtung). Dedupliziert über die PMID; das früheste Aufnahmedatum bleibt erhalten.

Die einzige Stelle, an der die Seite **nachlädt**: Der `<details>`-Ordner unter dem Studien-Frame holt die Datei per `fetch` — aber erst beim Aufklappen. Dadurch bleibt `index.html` schlank, während das Archiv beliebig wachsen darf. Beim Rendern werden die aktuell angezeigten PMIDs ausgeblendet, gruppiert wird nach `aufgenommen` (neueste zuerst).

`update_studies.py` schreibt die Datei bei jedem Lauf fort; der Workflow committet sie zusammen mit `index.html`.

### Newsletter-Feed & Download-Dateien

`scripts/build_newsletter.py` erzeugt aus `studien-archiv.json` fünf Dateien, die der Workflow mitcommittet:

| Datei | Zweck |
|---|---|
| `studien-feed.xml` | RSS 2.0 für Mailchimps RSS-to-Email. Ein `<item>` je Studie, **GUID = PMID** — dadurch versendet Mailchimp keine Studie doppelt. |
| `download/studien-aktuell.{docx,csv}` | nur der jüngste Tag |
| `download/studien-archiv.{docx,csv}` | der vollständige Bestand |

Das Skript liest **nur** das Archiv — kein API-Key, kein Netz. Es ist deshalb jederzeit einzeln aufrufbar (`py scripts/build_newsletter.py`, benötigt `python-docx`).

**Die Ausgabe ist bewusst deterministisch:** Alle Zeitstempel — `lastBuildDate`, der „Stand" im Word-Dokument, die Metadaten und sogar die ZIP-Einträge des docx (`normalize_docx()`) — werden aus dem Archivinhalt abgeleitet, nicht aus der Systemuhr. Zwei Läufe erzeugen bitgleiche Dateien. Ohne das entstünde täglich ein Commit samt Pages-Build, auch an Tagen ohne neue Studien. **Wer hier Zeitstempel einführt, bricht diese Eigenschaft.**

Das `pubDate` eines Items zieht den Rang sekundenweise **ab**: Mailchimp sortiert nach `pubDate`, die erste Studie der Tagesauswahl braucht also den spätesten Zeitstempel.

Einrichtung und Kampagnenvorlage: `NEWSLETTER-MAILCHIMP.md` und `newsletter/mailchimp-vorlage.html`. Die Vorlage ist Tabellen-Layout mit Inline-Styles — Outlook rendert mit der Word-Engine und kann kein modernes CSS. Sichtbarer Text dort **mit echten Umlauten** (die ASCII-Umschreibungen in den Python-Kommentaren sind eine Code-Konvention und gehören nicht in Lesertext).

### Studien aktualisieren

**Automatisch, täglich** — das ist der aktive Weg: `.github/workflows/update-studies.yml` läuft um 03:00 UTC — das sind 05:00 Uhr deutscher Sommerzeit, 04:00 Uhr Winterzeit — (und per *Run workflow* manuell), ruft `scripts/update_studies.py` auf → PubMed → Claude-API (`claude-haiku-4-5`, Secret `ANTHROPIC_API_KEY`) → Marker-Block ersetzen → commit & push. Einrichtung dokumentiert in `EINRICHTUNG-GITHUB-ACTIONS.md`.

**Manuell auf Zuruf** — der Slash-Command **`/studien-update`** (`~/.claude/commands/studien-update.md`): Claude recherchiert und formuliert selbst im Chat, ohne API-Key. Nützlich für Sonderfälle (anderer Suchbegriff, Zwischenstand), ersetzt aber nicht die Automatik.

`scripts/update-studies.ps1` und `scripts/Studien-aktualisieren.cmd` sind eine ältere lokale PowerShell-Variante und werden von der Automatik nicht verwendet.

## Geheimnisse im Repository

| Secret | Wofuer | Fehlt es? |
|---|---|---|
| `KLIMAHUB` | **Claude-API** fuer die Studienauswahl | Der taegliche Lauf bricht ab, `index.html` bleibt unveraendert |
| `KLIMAHUBMC` | **Mailchimp-API** fuer den Kampagnen-Entwurf (MC = Mailchimp) | Nur dieser Schritt entfaellt (`continue-on-error`) |

**Die beiden Namen unterscheiden sich um zwei Buchstaben.** Wer sie verwechselt, bekommt keine Fehlermeldung, die das sagt — sondern eine Authentifizierung, die beim jeweils anderen Dienst scheitert.

Der Workflow liest beide mit Rueckfallwegen:

```yaml
ANTHROPIC_API_KEY: ${{ secrets.KLIMAHUB   || secrets.ANTHROPIC_API_KEY }}
MAILCHIMP_API_KEY: ${{ secrets.KLIMAHUBMC || secrets.KNOWLEDGEHUB || secrets.MAILCHIMP_API_KEY }}
```

**Beim Anlegen eines weiteren Portals daran denken:** Der Name des Geheimnisses und der Name im Workflow muessen zusammenpassen; ein Tippfehler faellt erst beim naechtlichen Lauf auf.

### Beide Portale schreiben in dasselbe Mailchimp-Konto

Das ist die Fehlerquelle, die beim Aufsetzen zugeschlagen hat. `mailchimp_entwurf.py` erkennt seine eigenen Kampagnen **am Titel-Praefix** — Mailchimp fuehrt darueber kein Buch, seit die RSS-Automation weg ist.

| Portal | `PRAEFIX` |
|---|---|
| wissen.m-vf.de | `MVF Studien-Newsletter` |
| klima.m-vf.de | `MVF Klima-Newsletter` |

Mit dem geerbten Praefix meldete der erste Lauf hier „Entwurf besteht bereits" und legte gar keinen an — er hatte den Entwurf des Schwesterportals fuer seinen eigenen gehalten. Ein Zusatz reicht als Abhilfe **nicht**: `datum_aus_titel()` prueft mit `startswith()`, ein „MVF Studien-Newsletter Klima …" waere drueben als eigene Kampagne durchgegangen. Der Praefix muss vollstaendig eigen sein.

### Der Newsletter laedt alles von der eigenen Domain — auch das Logo

Logo, Schriften und die vier Download-Links im Newsletter zeigen auf `https://klima.m-vf.de/...`. **Das setzt ein gueltiges HTTPS-Zertifikat voraus.** Am 17.08.2026 fehlte das Logo in der Testausgabe, weil GitHub das Zertifikat fuer die frische Domain noch nicht ausgestellt hatte: E-Mail-Programme laden Bilder ausschliesslich ueber HTTPS, und ueber `http://` war alles laengst da (200, 23.551 Bytes) — nur eben nicht abrufbar fuer den Mail-Client.

Das Logo war dabei nur das Sichtbarste. Betroffen waren ebenso die Schriften und **alle vier Download-Links**, die still ins Leere gefuehrt haetten.

**Merke fuer ein weiteres Portal:** Erst das Zertifikat abwarten, dann den ersten Newsletter versenden. Und wenn im Newsletter etwas fehlt, zuerst pruefen, ob die Ressource ueber `https://` erreichbar ist — nicht die Vorlage durchsuchen.

## Newsletter-Anmeldung (`newsletter.html`)

Zwei Newsletter, eine Seite: Studien-Newsletter (taeglich, aus dem Hub) und MVF-Newsletter
(redaktionell). Gesendet wird **direkt an Mailchimp**, nicht an WordPress — das WPForms-Formular
auf m-vf.de minted sein Token je Seitenaufruf und liegt auf fremder Domain.

### Die Schwesterportale stehen mit auf der Seite

Seit dem 18.08.2026 bietet die Anmeldung auch die Studien-Newsletter der anderen Portale an.
Die Liste heisst `REIHE` und steht im Skriptteil von `newsletter.html` — **in jedem Portal
gleich**; das eigene Portal filtert sich anhand von `MC.gruppeStudien` selbst heraus. Gepflegt
wird sie in `portal-vorlage/vorlage/newsletter.html`, nicht hier: Die Datei ist seit demselben
Tag **neutral** und laeuft beim Abgleich mit. Portaleigen sind nur noch vier Platzhalter
(`META_NEWSLETTER`, `NL_STUDIEN_WAS`, `MC_GRUPPE_FELD`, `MC_TAG_STUDIEN`) aus `portal.json`.

Zwei Regeln dazu: `bereit: false` haelt ein Portal aus dem Angebot heraus, solange es noch nicht
versendet. Und `MC.tagStudien` darf **nie** `"0"` enthalten — in JavaScript ist die Zeichenkette
`"0"` wahr, und die Anmeldung schickte Mailchimp `tags=0`. Wo es keinen Tag gibt, bleibt das
Feld leer.

### Die drei Fallen, die uns je einen Anlauf gekostet haben

| Falle | Symptom | Loesung |
|---|---|---|
| Host ohne Kontovorsilbe | `us6.list-manage.com` → 404 | `monitor-versorgungsforschung.us6.list-manage.com` |
| `/subscribe/post-json` | 404 mit GET wie POST — JSONP ist abgeschaltet | gewoehnliches Formular-POST |
| Fehlendes `f_id` | Mailchimp nimmt an, **verwirft still** und leitet trotzdem auf die Dankeseite | Kennungen in die Adresszeile plus `f_id` |

**Die dritte ist die gemeinste:** Ohne `f_id` verlangt Mailchimp das Token `ht`, das nur die
gehostete Seite erzeugt. Fehlt beides, sieht alles nach Erfolg aus — Weiterleitung inklusive —,
und in der Zielgruppe kommt nichts an. **Eine Weiterleitung ist kein Beweis. Beweis ist der
Kontakt in Mailchimp.**

### Kennzeichen

```
tags=<noch einzutragen>  Studien-Newsletter Klima
group[16136][1024]=1     Studien Newsletter Klima  (Gruppe der Mailchimp-Anmeldeseite)
group[5629][4]=1         Monitor Versorgungsforschung Newsletter
group[5629][64]=1        Datenschutzerklaerung gelesen
```

**Die drei Studien-Newsletter liegen in je eigenen Gruppenmengen** (Stand 17.08.2026, auf der
gehosteten Anmeldeseite ausgelesen):

| Kennung | Gruppe | Portal |
|---|---|---|
| `group[16135][512]` | Studien Newsletter VF | wissen.m-vf.de |
| `group[16136][1024]` | Studien Newsletter Klima | **dieses Portal** |
| `group[16137][2048]` | Studien Newsletter KI | noch kein Portal |

> **Falle beim Nachschlagen:** Auf Mailchimps Anmeldeseite steht die Beschriftung **vor** dem
> Ankreuzfeld, nicht dahinter. Wer den HTML-Quelltext mit einem Suchfenster hinter dem `<input>`
> auswertet, ordnet alle drei Gruppen um eine Position verschoben zu — und traegt Anmeldungen in
> die falsche Liste ein. Beim Nachschlagen immer den vollstaendigen `<label>`-Block ansehen.

**Die Tag-Nummer fehlt noch.** Sie steht in der Adresszeile, wenn man den Tag „Studien-Newsletter
Klima" in Mailchimp anklickt (dort als `static_segment`). Einzutragen an **zwei** Stellen:
`newsletter.html` (`MC.tagStudien`) und `scripts/mailchimp_entwurf.py` (`TAG_ID`). Solange sie
fehlt, laeuft alles ueber die Gruppe — der Versand funktioniert, ist aber weniger fein steuerbar.
Beide Stellen sind gegen die fehlende Nummer abgesichert: Das Formular schickt dann kein leeres
`tags`-Feld, und das Entwurfsskript setzt keine ungueltige Segmentbedingung.

**Der Studien-Newsletter traegt zwei Kennzeichen, und das mit Absicht:** Mailchimps eigene
Anmeldeseite kann nur Gruppen setzen, nicht Tags. Wer sich dort eintraegt, haette ohne die
Gruppe kein Kennzeichen; wer sich hier eintraegt, ohne den Tag keines im anderen Segment.
Beide Wege setzen darum beides.

> **Vorsicht bei Gruppen:** Am 17.08.2026 wurde `group[5629][1]` — bis dahin „Pharma Relations
> Newsletter" — in „Studien Newsletter VF" **umbenannt** statt neu angelegt. Damit trug schlagartig
> jeder Alt-Abonnent von Pharma Relations das Kennzeichen des Studien-Newsletters. Zurueckbenannt
> und als eigene Gruppenmenge 16135 neu angelegt. **Gruppen-Nummern sind Identitaeten, keine
> Beschriftungen** — wer eine umbenennt, verschiebt Menschen, nicht Woerter.

Der MVF-Newsletter bleibt bei der **Gruppe**, weil seine Abonnenten sie seit jeher tragen und das
Formular auf m-vf.de sie weiter setzt; ein zusaetzlicher Tag waere nur bei Neuzugaengen vom Hub
gesetzt. Der Studien-Newsletter ist neu und kommt nur von hier — dort ist der **Tag** von Anfang
an vollstaendig. In derselben Gruppenmenge stehen noch Pharma Relations, MarketAccess&HealthPolicy
und Monitor Pflege: **Altlasten, die es im Verlag nicht mehr gibt.** Nicht anbieten.

### Warum ein unsichtbarer Rahmen

Mailchimp leitet nach der Anmeldung auf die zielgruppenweite Dankeseite — bei MVF auf m-vf.de.
Der Besucher landete also auf einer fremden Seite. Deshalb geht das Formular in ein verstecktes
`<iframe>`; die Bestaetigung steht im MVF-Design auf der Seite. Was im Rahmen passiert, ist nicht
lesbar (fremde Domain), **darum ist die Bestaetigung so formuliert, dass sie auch dann stimmt,
wenn die Adresse schon eingetragen war.** Keine Erfolgsmeldung fuer etwas, das nicht stattfand.

### Was ausserhalb des Codes liegt

- Double-Opt-in ist aktiv; die Bestaetigungsmail landete bei Gmail zunaechst **im Spam** (englischer
  Betreff, Domain nicht authentifiziert). Beides in Mailchimp zu pflegen, nicht hier.
- Die RSS-Kampagne muss auf die Gruppe *Studien Newsletter Klima* (bzw. spaeter den gleichnamigen Tag) gefiltert sein — sonst bekaemen
  alle 5.905 Abonnenten taeglich die Studienauswahl.
- Die Datenschutzhinweise in `index.html` (Abschnitte 3 und 4) beschreiben genau dieses Verfahren.
  **Wer den Anmeldeweg aendert, muss sie mitaendern.**

## Gestaltung: das Erscheinungsbild von m-vf.de

Der Hub übernimmt seit August 2026 Schrift und Farben von **monitor-versorgungsforschung.de** (die Kurzadresse `m-vf.de` leitet dorthin um).

| Merkmal | Wert | Herkunft |
|---|---|---|
| Schrift | **Lato** 300/400/700 | dieselbe wie auf m-vf.de, dort ebenfalls selbst gehostet |
| Hausfarbe | `#0051A1` | Kopfbereich und Akzente der MVF-Seite |
| Handlungsfarbe | `#BE9E53` | die goldenen Knöpfe („Abonnieren", „Alle News") |
| Seitengrund | `#EDF2FA` | Flächenfarbe der MVF-Seite |
| Eckradien | 5–6 px | MVF nutzt 5–6 px |

Das Logo (`logo/mvf-logo.png`) besteht aus **genau zwei Farben**: Blau `#0060A0` und Gold `#C0A060` — es bestätigt die Palette.

**Regeln, die nicht beiläufig gebrochen werden sollten:**

- **Nur Lato.** Keine zweite Schriftfamilie. Die Klasse `.mono` erzeugt ihren technischen Charakter über `font-variant-numeric:tabular-nums`, nicht über eine Monospace-Schrift; `.serif` ist auf `inherit` gesetzt. MVF nutzt durchgängig Lato, auch in Überschriften.
- **Schriften liegen in `fonts/` und werden selbst ausgeliefert.** Kein Google Fonts: Das wäre ein Verbindungsaufbau zu Dritten und widerspräche den Datenschutzhinweisen. MVF macht es genauso.
- **Nur die Stärken 300/400/700 existieren.** Zwischenstärken wie 600 lässt der Browser auf 700 einrasten — deshalb überall direkt 700 setzen.
- **Gold nur auf Knöpfen.** Als kleine Textfarbe erreicht `#BE9E53` nur 3,0:1. Aus demselben Grund trägt die Knopfschrift auf Gold **dunkles** `#2A2207` (6,2:1) und nicht Weiß — die MVF-Seite selbst nutzt dort Weiß mit 2,6:1, das wird bewusst nicht übernommen.
- **Das Logo wird im Dark Mode nicht umgefärbt**, sondern auf eine weiße Fläche gestellt. Ein `filter:invert` würde den Goldanteil der Wortmarke tilgen.
- **Kategorien tragen alle die Hausfarbe.** Das `--h`-System in `CATS` besteht weiter, wird aber von `.cat{ --cat:var(--brand); }` überschrieben — ein Regenbogen widerspräche der Zweifarbigkeit. Eine Zeile genügt, um die Farbcodierung zurückzuholen.

## Der Datenbestand: was hier anders ist als im Schwesterportal

| | Anzahl |
|---|---|
| Datenbanken gesamt | 89 in 9 Rubriken |
| `live` (Deeplink mit `%s`) | 40 |
| `portal` (feste URL) | 41 |
| `lic` (lizenzpflichtig) | 8 |

Rund 30 Eintraege sind aus dem Schwesterportal uebernommen (PubMed, Cochrane, Open-Access-Discovery, Volltextbeschaffung), der Rest ist neu.

**Zwei Dinge gibt es hier, die es dort nicht gibt:**

1. **Rubrik `klimadaten`** — Messreihen, Modelle und Indikatoren statt Publikationen (Copernicus, IPCC, EEA, NOAA, EM-DAT, GBD). Sie traegt als einzige ein `hinweis`-Feld in `CATS`, weil ein Suchbegriff dort zu Datensaetzen fuehrt und nicht zu Studien. Wer diesen Hinweis entfernt, laesst Nutzer in eine Erwartungsluecke laufen.
2. **Rubrik `praxis`** — Hitzeaktionsplaene, Kommunen, Kliniken. In diesem Feld steht ein grosser Teil des verwertbaren Wissens in Behoerdenberichten, nicht in Zeitschriften; deshalb sind die Institutionen-Rubriken hier deutlich staerker besetzt.

### Beim Aufnehmen neuer Quellen geprueft — und was dabei herauskam

Der `%s`-Deeplink jeder neuen Quelle wurde am 17.08.2026 per HTTP-Abruf getestet. Drei Muster traten auf:

- **Funktioniert** (als `live` aufgenommen): KLUG, ECDC, IPCC, GHHIN, Lancet Countdown, Climate-ADAPT, EEA, Environmental Evidence Journal, DWD, Copernicus CDS, Bundesaerztekammer, World Weather Attribution, Health Care Without Harm.
- **Bot-Sperre (403)** — die Suche funktioniert im Browser, ist maschinell aber nicht beweisbar: US CDC, Campbell Collaboration (Wiley). Als `live` aufgenommen, im Browser gegenzupruefen.
- **Reine JavaScript-Oberflaeche oder Suchpfad nicht auffindbar** (als `portal` aufgenommen): US EPA, 3ie, EarthArXiv, ESS Open Archive, Umweltbundesamt, klimaanpassung.de.

**Beim Umweltbundesamt wurde der Pfad der Volltextsuche nicht gefunden** — mehrere plausible Muster (`/suche`, `/suchen`, `tx_solr`) liefern 404. Die Kachel zeigt deshalb auf die Themenseite Klimafolgen. Wer den echten Suchpfad findet, kann sie auf `live` umstellen.

## Studienfelder: `author`, `pubdate`, `added`

Alle drei stammen **nicht vom Sprachmodell**, sondern aus PubMeds `esummary` (`fetch_meta()` in `update_studies.py`) — es sind Fakten, keine Interpretation.

Beim Publikationsdatum wird die **genaueste echte** Angabe aus `pubdate` und `epubdate` genommen. `sortpubdate` ist bewusst ungenutzt: Bei reinen Monatsangaben setzt PubMed dort den 1. ein und täuscht damit einen Tag vor. Fehlt der Tag, steht `Aug. 2026` statt eines erfundenen Datums.

### Zwei Daten, die nicht verwechselt werden dürfen

| Feld | Bedeutung |
|---|---|
| `pubdate` | wann die Studie **erschienen** ist |
| `added` | wann **PubMed sie aufgenommen** hat (`history`, `pubstatus: entrez`) |

`esearch` wählt mit `sort=date` nach dem **Aufnahmedatum** aus — nicht nach dem Erscheinungsdatum. Beide liegen oft Wochen auseinander: Eine am 24.07. erschienene Arbeit kann erst am 14.08. in PubMed landen. Deshalb enthält eine Tagesauswahl regelmäßig ältere Publikationsdaten; das ist **kein Fehler**. Die Karte blendet `added` nur ein, wenn es von `pubdate` abweicht — sonst wäre es Rauschen.

Zum Sortieren dient `_sortschluessel()` (ISO-Datum aus den Rohfeldern), **nicht** der deutsche Anzeigetext. Sortiert wird in `main()`, nicht vom Modell: Aus einem Abstract lässt sich kein verlässliches Datum lesen, weshalb früher ältere Studien zwischen neueren standen.

## `SNAP_STATUS`: drei unterscheidbare Zustände

Die Seite soll nicht bloß „aktuell" behaupten, sondern sagen können, was zuletzt geschah:

| Zustand | Erkennung | Anzeige |
|---|---|---|
| normal | `SNAP_STATUS === "neu"` und Zeitstempel frisch | keine Meldung |
| Lauf ohne neue Studien | `SNAP_STATUS === "unveraendert"` | neutraler Hinweis |
| Lauf ausgefallen | `SNAP_DATE` älter als 30 Stunden | Warnhinweis |

`update_studies.py` setzt `SNAP_STATUS`, indem es die neuen PMIDs mit denen in der bestehenden `index.html` vergleicht. Den Ausfall erkennt die **Seite selbst** am Alter von `SNAP_DATE` — das funktioniert auch dann, wenn das Skript gar nicht erst lief. Die Auswertung ist gegen ein fehlendes `SNAP_STATUS` abgesichert (`typeof`), damit ältere Marker-Blöcke die Seite nicht brechen.

**Die Überschrift trägt bewusst kein Datum.** Um 06:00 Uhr hat der laufende Tag in PubMed praktisch nie schon Einträge — „Neu aufgenommen am [heute]" wäre fast täglich falsch, und das echte Datum stünde dauerhaft einen Tag zurück. Die Aktualität trägt stattdessen die Zeile „Zuletzt aktualisiert"; sie bezieht sich auf den Lauf und stimmt immer.

## Die Suche: nichts geschieht vor dem Absenden

Frueher schrieb die Seite die Links schon beim Tippen um — unsichtbar, ohne Rueckmeldung — und die Eingabetaste oeffnete ausgerechnet das MVF-Archiv, also eine von vielen Datenbanken. Beides ist abgeschafft.

| Aktion | Wirkung |
|---|---|
| Tippen | nichts; die Kacheln zeigen weiter auf die Startseiten |
| Enter, Knopf oder Schnellwahl-Chip | `suchen()`: Links vorbereiten, Ergebnisleiste einblenden, zur ersten **sichtbaren** Rubrik springen |
| Filter aendern | `zeigeErgebnis()`: nur die Zahlen nachfuehren |

**Die Trennung von `suchen()` und `zeigeErgebnis()` ist die Pointe.** Ruft `filtern()` am Ende `suchen()` auf, springt die Seite bei jedem Filterklick nach unten — mehrere Filter zu setzen wird dann unmoeglich. Gesprungen wird ausschliesslich beim Absenden.

### Filter

`FILTER` haelt vier Gruppen: `zugang`, `suchart`, `bool`, `rubrik` (Mehrfachauswahl als `Set`). `filtern()` blendet Kacheln aus, versteckt leere Rubriken samt Sprungmarke und fuehrt Zaehler, Plakette und Ruecksetz-Knopf nach.

**Vier Filter zusammen koennen alles ausblenden** — etwa „frei" + „Boolesch" + Rubrik „deutsch", denn keine deutsche Datenbank ist als boolesch belegt. Dafuer gibt es `#leerHinweis`; ohne ihn staende die Seite leer da.

### Boolesche Operatoren: `b` ist dreiwertig

| Wert | Bedeutung | Kennzeichen |
|---|---|---|
| `b:1` | geprueft, Operatoren wirken | `AND/OR ✓` |
| `b:0` | geprueft, wirken nicht | `AND/OR ✗` |
| fehlt | **ungeprueft** | keines |

Die Werte stammen aus einer Messreihe: dieselbe Suche mit `OR` und mit `AND`, verglichen mit einem Phantasiewort. Wertet eine Datenbank `OR` aus, steigt die Trefferzahl sprunghaft; wertet sie `AND` aus, faellt sie auf null.

**Stand hier: 9 ja, 3 nein, 28 ungeprueft** (gezaehlt werden nur die 40 Live-Datenbanken — bei Portal-Kacheln ist die Frage gegenstandslos). Die geprueften Werte sind aus dem Schwesterportal uebernommen und gelten fuer dieselben Datenbanken (PubMed, Europe PMC, Google Scholar, OpenAlex …). Die neu aufgenommenen Klima- und Institutionenquellen sind **saemtlich ungeprueft** — bei ihnen wurde bislang nur getestet, ob der `%s`-Deeplink ueberhaupt greift, nicht wie sie Operatoren behandeln.

**Ungeprueftes zaehlt nie als „kann es".** Der Beschreibungstext sagt ausdruecklich, dass ein fehlendes Zeichen Unwissen bedeutet, nicht Unvermoegen — sonst wuerden Datenbanken wie Cochrane faelschlich festgelegt.

### Hinweise unter dem Suchfeld

Zweispaltig ueber **CSS-Textspalten** (`columns:2`), nicht ueber ein Raster. Ein Raster richtet Zeilen an der hoechsten Karte aus und streckte den kurzen Absatz, was knapp 50 px Loch hinterliess. Textspalten packen dicht. Unter 760 px einspaltig.

## Studienauswahl: kein Algorithmus, ein Prompt

Es gibt **keine Gewichtung und kein Ranking**. PubMed liefert Kandidaten, ein Sprachmodell waehlt daraus nach schriftlichen Kriterien aus. Wer die Auswahl aendern will, aendert `USER_TEMPLATE` in `update_studies.py` — nicht Code.

### Die Suchabfrage: zwei Seiten, die beide zutreffen muessen

`TERM` setzt sich aus `_UMWELT` **UND** `_GESUNDHEIT` **UND** `Humans[MeSH Terms]` zusammen. Alle drei Teile sind noetig:

- Ohne die Gesundheitsseite kommt reine Klimaphysik herein.
- Ohne die Umweltseite kommt reine Klinik herein.
- **Ohne `Humans[MeSH]` kommen Algenbluete, Emissionsbilanzen und Tierstudien herein** — sie erfuellen beide anderen Seiten formal, weil „health" und „mortality" in fast jedem Abstract vorkommen. Das war beim Bau der groesste Rauschfaktor; die Klammer nicht entfernen.

Gemessen am 17.08.2026: rund 89.000 Treffer gesamt, 14.600 mit Europa-/Deutschlandbezug.

### Zwei Abfragen statt einer

`fetch_pubmed()` fragt zweimal: `TERM` (40 neueste) und `TERM_DE` (15 neueste, zusaetzlich gefiltert auf `Germany` **oder `Europe`**), zusammengefuehrt und ueber die PMID entdoppelt.

**Warum Europa und nicht nur Deutschland:** Bei diesem Thema ist die Klimazone wichtiger als die Landesgrenze. Eine niederlaendische oder daenische Hitzestudie ist fuer Deutschland naeher dran als eine deutsche Methodenarbeit ohne Klimabezug. Der Europa-Filter haelt den Kandidatenpool in der richtigen Klimazone.

### Uebertragbarkeit ist das oberste Kriterium — und hat hier zwei Achsen

Anders als im Schwesterportal genuegt der Systemkontext nicht. Der Prompt bewertet zusaetzlich die **Klimazone**:

- **Klimatisch:** gemaessigte Breiten uebertragbar; Tropen, Subtropen und aride Regionen nicht. Temperaturschwellen und der Anpassungsgrad der Bevoelkerung sind dort grundverschieden — eine Studie aus Suedasien zur Hitzesterblichkeit sagt ueber Deutschland fast nichts.
- **Strukturell:** wie im Schwesterportal (DACH und Sozialversicherungslaender hoch, steuerfinanzierte mittel, USA gering).

**Beides muss stimmen.** Eine US-Studie aus einer gemaessigten Klimazone kann taugen, eine franzoesische Studie aus Ueberseegebieten nicht.

Jede Studie traegt das Feld `transfer`: ein Halbsatz, worauf die Uebertragbarkeit beruht. Es steht auf der Karte, im Archiv, in beiden Downloads und im Feed. Ohne dieses Feld waere das Kriterium unpruefbar — man muesste glauben, dass es wirkt.

Altbestand hat `transfer` nicht; die Anzeige laesst die Zeile dann weg.

## Suchglossar: deutsch suchen, international finden

`GLOSSAR` steht als Konstante **in** `index.html` (237 Begriffe, rund 13 KB) — nicht nachgeladen, weil es bei jeder Suche gebraucht wird. Die Pflegefassung mit Sachgebieten liegt als `_glossar.json` auf `entwurf/suche`.

Bewusst **kein Uebersetzungsdienst**: Ein Schluessel fuer DeepL oder Google muesste im Quelltext stehen und waere damit oeffentlich. Ein gepflegtes Verzeichnis ist ausserdem redaktionell kontrollierbar.

**Massgeblich ist der Suchbegriff, nicht die woertliche Uebersetzung:** `Hitzeaktionsplan` → `heat health action plan` (nicht *heat action plan*), `Waermeinsel` → `urban heat island`, `Uebersterblichkeit` → `excess mortality`, `gefuehlte Temperatur` → `perceived temperature`.

`uebersetze()` ersetzt wortweise, **laengste Begriffe zuerst** — sonst zerfaellt „staedtische Waermeinsel" in „Waermeinsel" und „hitzebedingte Sterblichkeit" in „Sterblichkeit". Wortgrenzen ueber Leerzeichen und Komma statt ``, weil `` bei Umlauten und Bindestrichen falsch trennt.

`istDeutsch()` entscheidet, welche Datenbank den deutschen Begriff behaelt: Rubrik `deutsch` plus einzeln mit `de:1` gekennzeichnete Kataloge (Nationallizenzen, subito, K10plus, DRKS, klimaanpassung.de). Alle uebrigen bekommen die uebersetzte Fassung.

Sichtbar gemacht wird das in der Ergebnisleiste („In internationalen Datenbanken wird gesucht als …"); der Schalter `#uebersetzenAn` stellt es ab und wirkt sofort, ohne Sprung.

## Versand: Torwächter und Veto-Fenster

Seit dem 18.08.2026 wird der Newsletter **nicht mehr von Hand freigegeben**. Der nächtliche Lauf legt den Entwurf an, `scripts/torwaechter.py` prüft ihn, und `mailchimp_entwurf.py` **terminiert** die Kampagne auf `TERMIN_LOKAL` (10:00 Uhr deutscher Ortszeit — bewusst nicht in UTC festgeschrieben, sonst verschöbe die Zeitumstellung den Versand). Bis dahin lässt sie sich in Mailchimp mit einem Klick absagen — *Unschedule*.

Der Grund für diese Bauweise: Versand ist der einzige Schritt der Kette, der sich nicht zurücknehmen lässt. Der Torwächter fängt **mechanischen** Unfug (fehlende Felder, Platzhalter, erfundene Zeitschriften — gegen PubMed geprüft, englisch gebliebene Zusammenfassungen, Dubletten, leeres Empfängersegment). Er fängt **nicht** die Zusammenfassung, die flüssig klingt und die Studie falsch wiedergibt; dafür ist das Zeitfenster da.

**Schlägt eine Prüfung an, wird nicht terminiert.** Der Entwurf bleibt liegen, die Redaktion bekommt die Testausgabe mit Freigabekasten, und der Grund steht in `versand-status.json`. Lieber ein Tag ohne Newsletter als ein falscher.

**Der Torwächter arbeitet in zwei Stufen** (seit 24.08.2026). `vorpruefung()` sortiert einzelne missglückte Studien aus, bevor die Ausgabe gebaut wird — ein zu langer Titel an einer Studie soll nicht sieben einwandfreie mitnehmen. Fällt mehr als ein Drittel weg oder bleiben weniger als zwei übrig, stoppt stattdessen die ganze Ausgabe. Danach entscheidet `pruefe()` über die Ausgabe als Ganzes; **die Abgleiche gegen PubMed stoppen weiterhin hart** — eine falsche Zeitschrift ist kein Formfehler, sondern ein Rückfall im Mechanismus. Was aussortiert wurde, steht in `versand-status.json` und im Sammelbericht: Aussortieren ist der stille Fall, und still darf er nicht bleiben.

`versand-status.json` wird vom Workflow mitcommittet. Das Repo `mvf-portal/knowledge-hubs` liest sie von allen Portalen ein und macht daraus **eine** GitHub-Issue statt fünf E-Mails (`scripts/versand_bericht.py`, täglich 03:45 UTC).

Qualitative Interviewstudien und Expertenpapiere sind ausdrücklich **zugelassen** — der Torwächter verlangt deshalb Substanz im Ergebnisfeld, aber keine Zahl.

## Fallstricke

- **`const` vor seiner Definition benutzen legt die ganze Seite lahm.** Beim Einbau des Glossars stand `document.getElementById('cntGlossar').textContent = GLOSSAR.length;` vor der `const GLOSSAR`-Zeile. `const` wird zwar hochgezogen, ist davor aber nicht benutzbar — die Folge war ein `ReferenceError`, und weil das gesamte Skript in einem Block liegt, wurden **weder Kacheln noch Studien** gerendert. Nach Aenderungen am Skriptteil immer die Konsole pruefen.

- **Kein HTML-Escaping.** Alle Inhalte werden per `innerHTML`-Stringkonkatenation eingesetzt. Texte mit `<`, `>` oder `&` zerlegen das Markup — beim Anlegen von `DB`- oder `STUDIES`-Einträgen vermeiden bzw. maskieren.
- **Keine geraden doppelten Anführungszeichen in `STUDIES`-Strings** — die Objekte stehen in inline-JS; ein `"` bricht das Skript und die Seite bleibt leer. Notfalls „…" oder Klammern verwenden.
- **Deutsches Zahlenformat** in Studientexten (`0,63` statt `0.63`).
- **Impressum und Datenschutzhinweise im Footer sind rechtlich erforderlich** (§ 5 DDG, § 18 Abs. 2 MStV) und inhaltlich mit dem Betreiber abgestimmt — nicht beiläufig umformulieren. Die Datenschutzhinweise beschreiben bewusst eine statische Seite ohne Cookies/Tracking; das muss stimmen, wenn Skripte hinzukommen.
- **Ein Fehler bricht die ganze Seite.** Da das gesamte JS inline in einem `<script>`-Block liegt, macht ein Syntaxfehler die Seite komplett leer (Kacheln *und* Studien werden per JS erzeugt). Nach Änderungen am Skriptteil immer die Live-Seite prüfen.
- **Dark Mode.** Farben laufen über CSS-Variablen mit drei Quellen: `prefers-color-scheme`, `:root[data-theme="dark"]` und `:root[data-theme="light"]`. Neue Farbwerte in allen relevanten Blöcken ergänzen, nicht nur im Light-Default.
