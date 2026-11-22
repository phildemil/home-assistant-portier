# Portier – Notizen für Claude Code

Video-Türklingel als Home-Assistant-Integration (`custom_components/portier`). Entstanden als
Neubau der Idee aus `phildemil/home-assistant-bell` (nicht kopiert). Benutzer spricht Deutsch,
Oberfläche ist Deutsch (Englisch als Übersetzung vorhanden).

## Was Portier macht
- Pro Tür ein Config-Entry: Klingeltaster (binary_sensor, event, input_button, button, switch,
  input_boolean), Kamera (jede HA-Kamera), Türöffner (switch/light/input_boolean als Impuls,
  lock → open/unlock, button → press, script, cover), optional Türkontakt und Gong.
- Klingeln → Overlay mit Livebild auf jeder offenen HA-Seite, Browser-Ton, Push an
  `notify.mobile_app_*` (Kamerabild + Knopf „Tür öffnen“; Tippen auf die Nachricht öffnet
  `/portier?show=<entry_id>` = Livebild-Popup dieser Tür), Telegram (`send_photo` mit
  Inline-Knopf, nur ausgewählte Chats dürfen öffnen). Öffnen-Knöpfe sind Einmal-Token mit
  Ablaufzeit. Nach Öffnen/Ablauf wird die Push-Nachricht (gleicher `tag`) still ersetzt
  (Kanal „Türklingel Info“ low, `alert_once`, iOS passive) – wie das Telegram-Bearbeiten.
- Telegram-Empfänger werden im Editor per Haken aus den `allowed_chat_ids`-Subentries der
  Telegram-Bot-Integration gewählt (`portier/config` liefert `telegram_chats`); gespeichert
  bleibt `telegram_targets` als kommagetrennte IDs.
- Seitenleiste „Türklingel“: Türen anlegen/bearbeiten/entfernen (nur Admin), Standbild,
  Live, Testklingel, Verlauf, Einstellungen pro Gerät (Overlay/Ton an/aus).
- Popup und Türkarten: das Kamerabild ist die ganze Karte, Name, Status, Knöpfe und Türöffner
  liegen als Overlay im Bild (`PICTURE_CSS`, Klasse `.plate.pic`).
- Gegensprechen (Option `talk` pro Tür, Standard aus): WebRTC-Angebot mit Audio `sendrecv`,
  Mikrofon wird nur beim Gedrückthalten per `replaceTrack` angehängt (keine Neuverhandlung).
  Antwort-SDP entscheidet, ob die Kamera Ton annimmt (`answerAcceptsAudio`). Knöpfe:
  Lautsprecher (Kameraton an/aus) + Sprechen (`wireTalk`). Braucht https (Browser gibt das
  Mikrofon sonst nicht frei) – dann steht „Nur mit HTTPS“ am Knopf. Mit echter Kamera noch
  ungetestet; geprüft nur mit simulierter WebRTC-Gegenstelle im Browser.

- Öffnen per NFC (`nfc.py`): Event `tag_scanned` der HA-App; Optionen `nfc_tags` (Tag-Entitäten),
  `nfc_devices` (erlaubte mobile_app-Geräte), Zeitfenster, Hinweis an Empfänger. Handy braucht
  Verbindung zu HA (Benutzer: WLAN bis zur Tür, kein Fernzugriff).
- Farben (`look.py`, Store `portier.look`, `portier/look/save` nur Admin): Standard / Wie System
  (`--primary-color`/`--accent-color` des HA-Designs) / Eigene; gilt für alle Geräte, kommt im Snapshot
  (`look`) und wird als CSS-Variablen `--pt-brass*`/`--pt-verdigris*` auf Overlay und Panel gesetzt.
- Livebild vorwärmen (`preload.py`, Option `camera_preload`): **eine Stelle der Wahrheit** = HAs Kamera-
  Einstellung `preload_stream`. Der Editor liest sie (`camera/get_prefs`), neu gewählte Kamera → Vorschlag an,
  Kameras ohne STREAM-Feature → Haken ausgeblendet. Gesetzt wird nur beim Speichern (`ws_door_save`, startet
  den Stream sofort), nie beim HA-Start. Kamera gewechselt oder Tür gelöscht → alte Kamera aus, wenn keine
  andere Tür sie vorwärmt. Kamera-Modul nur lazy importiert (sonst bricht Portier ohne Kamera-Bibliotheken).
- Verläufe: Klingelverlauf (Taster/Person) und Türöffner-Verlauf (wer, Weg), beide im Store (je 50).
  Wortwahl „Weg“: Livebild, Push- und Telegram-Meldung sagen für Push und HA-App kurz „App“;
  nur der Türöffner-Verlauf ist genau (Push-Nachricht / Home-Assistant-App / Browser).

## Wichtige Entscheidungen des Benutzers (nicht wieder ändern)
- Allgemein bauen: jedes Relais, jede Kamera, keine Shelly-Sonderlogik.
- Türöffner reagiert auf **einen Tipp**, kein Gedrückthalten.
- **Kein Nachholen** verpasster Klingelereignisse. Es muss einfach im richtigen Moment laufen.
  Livebild und Ton nur, wenn die Seite/App **beim Klingeln** sichtbar war und die Meldung
  höchstens 5 s alt ist (`LATE_MS`). Einzige Ausnahme: war die Seite sichtbar, aber das Abo
  gerade kurz weg (Neuverbindung), holt der nächste Snapshot es nach – innerhalb dieser 5 s.
  App erst nach dem Klingeln geöffnet → nichts, nur die Push-Nachricht.
- Kein Symbol ins Push-Bild malen: das Bild ist das reine Kamerabild; Android erlaubt
  anklickbare Knöpfe nur unter dem Bild.
- Es darf **nie F5 nötig** sein – auch nicht nach HA-Neustart, Stromausfall oder Update.
  Einzige Ausnahme: Seiten/Apps, die schon **vor** dem Anlegen der ersten Tür offen waren, einmal
  neu laden (technisch nicht vermeidbar, steht im Fertig-Dialog).
- Nach dem HACS-Neustart darf **kein zweiter Neustart** nötig sein.
- Mehrere Livebilder: im Querformat nebeneinander, kleiner je mehr; hochkant untereinander.
- Schließen eines **Klingel**-Overlays (×) schließt es auf allen Geräten; von Hand geöffnete
  Kameraansichten nur lokal. Ein altes × schließt nie ein neueres Klingeln.
- Browser-Klingelton ist zweitrangig (echter Gong hängt meist am Relais), Auswahl bleibt.
- `notify.notify` wird im Editor ausgeblendet (würde Push doppelt senden).
- 2-Wege-Audio bleibt in Home Assistant (kein Standalone-Eigenbau, vom Benutzer verworfen).

## Architektur
- `manager.py` – `PortierDoor`: Trigger-Erkennung (Flanke, Event-Typ-Filter, Entprellung),
  Klingeln, Öffnen mit Impuls. Ausschalt-Pflicht wird **vor** dem Einschalten gespeichert
  (`Store`), nach Neustart wird abgeschaltet; Fehlschläge werden wiederholt + Notification.
- `notifications.py` – Push/Telegram, Einmal-Token, Telegram-Chat-Prüfung.
- `websocket.py` – `portier/subscribe` (Snapshot, ring, dismiss, show), `open`, `ring`,
  `dismiss`, `config`, `door/save`, `door/delete` (die letzten drei nur Admin).
- `config_flow.py` – Einrichtung fragt nur den Namen (Fertig-Dialog verlinkt `/portier?edit=new`),
  „Konfigurieren“ ist ein Abbruch mit Link `/portier?edit=<entry_id>`. Einziger Einstellort ist der
  Editor in der Seitenleiste (`PANEL_SCHEMA`/`panel_options`). Alle Geräte sind optional.
- `frontend.py` – Static Path `/portier_static` (ohne Cache-Header), Panel, `add_extra_js_url`,
  Endpoint `/portier_static/current`. `async_ensure_loader` (bei jeder Tür-Einrichtung) schreibt
  `config/www/portier/loader.js` und trägt per `yaml_setup.py` in `configuration.yaml` ein:
  `frontend: extra_module_url: [/local/portier/loader.js]` (Backup + Validierung) – automatisch,
  ohne Neustart (jeder HA-Start liest die Zeile). Nur wenn das nicht sicher geht: Hinweis
  `loader_missing` (ohne Knopf). Letzte Tür gelöscht → Zeile + Lader wieder entfernt.
- `frontend/portier.js` – ein Modul: Controller (Abo, Wiederanmeldung bei `ready` und
  `component_loaded`, Selbst-Reload bei neuer Version), Overlay, Kamera (Standbild sofort, dann
  WebRTC über `camera/webrtc/offer` und HLS **gleichzeitig**, das erste mit echten Bildern
  gewinnt, WebRTC übernimmt auch später noch; sonst MJPEG), Editor, Panel.
  Wächter in `CameraView.checkAlive` (alle 2 s, nur sichtbar): pausiert → play, steht → Neustart,
  HLS > 6 s hinten → an die Live-Kante springen, nichts geht → Neustart mit Pause bis 30 s. Zurück
  aus dem Hintergrund (≥ 3 s): HLS neu, WebRTC nur wenn es steht. Türklingel-Seite merkt sich offene
  Livebilder (`panelLive`) und startet sie nach einem Seitenwechsel wieder.

## Warum der Lader in configuration.yaml nötig ist
HA liefert Seiten aus, bevor Integrationen geladen sind. `add_extra_js_url` erreicht Seiten,
die in diesem Fenster geöffnet wurden (Stromausfall, App-Neuverbindung), nie mehr; HA lädt es
nicht nach. Dashboard-Ressourcen helfen nicht (die neue Übersicht lädt sie nicht).
`/local` (= `config/www`) ist ab Sekunde 1 verfügbar; der Lader fragt `/portier_static/current`
ab, bis Portier läuft, und importiert dann das Modul. Selbes Vorgehen wie card-mod.

## Testen
- `pytest` mit `pytest-homeassistant-custom-component` (Tests in `tests/`). Lokal unter Windows:
  `tools\run-tests.ps1` (legt `tools\.venv` mit Python 3.14 an; `tools\winshim` ersetzt die
  Linux-Module `fcntl`/`resource` und lockert die Socket-Sperre). Nach jeder Änderung laufen lassen.
- GitHub Actions (`.github/workflows/tests.yml`): pytest, JS-Syntax + Versionsgleichheit
  manifest/portier.js, hassfest, HACS-Prüfung.
- Echter Test: HA in einem venv mit `hass -c <config>`, Playwright für Overlay/Editor.
  Kritische Szenarien: Seite **während** des HA-Starts geladen, HA-Neustart bei offener Seite,
  mehrfaches Klingeln, zwei Geräte gleichzeitig (synchrones Schließen).
- Benutzer-HA: 2026.11.x, Zugangsdaten und Pfade stehen in der lokalen Claude-Memory (nicht im Repo).
- Bei jeder Änderung `version` in `manifest.json` **und** `VERSION` in `frontend/portier.js` erhöhen.
