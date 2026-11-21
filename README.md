# Portier – Video-Türklingel für Home Assistant

Wenn es klingelt, schwebt über jeder offenen Home-Assistant-Seite das Livebild der Tür ein. Gleichzeitig geht eine Push-Nachricht mit Kamerabild und „Tür öffnen“-Knopf aufs Handy und zu Telegram. Geöffnet wird mit einem Tipp auf den Messing-Taster.

Portier arbeitet mit dem, was du schon in Home Assistant hast:

| Aufgabe | Funktioniert mit |
| --- | --- |
| Klingeltaster | Binärsensor, Event-Entität (z. B. Shelly-Eingang), Taste, Schalter, Helfer |
| Kamera | jede Kamera in Home Assistant |
| Türöffner | Schalter / Licht / Helfer (Impuls), Schloss (öffnen), Taste (drücken), Skript, Tor |
| Gong (optional) | Schalter, Licht, Taste, Skript, Sirene |
| Türkontakt (optional) | Binärsensor, Helfer, Schloss, Tor |

## Installation

1. Über HACS installieren (oder den Ordner `custom_components/portier` nach `/config/custom_components/portier` kopieren).
2. Home Assistant neu starten. Das ist der einzige Neustart.
3. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Portier** und den Namen der Tür eingeben.
4. Im Fertig-Dialog auf **Tür jetzt einrichten** tippen: Auf der Seite **Türklingel** in der Seitenleiste öffnet sich der Editor für Klingeltaster, Kamera, Türöffner, Benachrichtigungen usw. Alle Einstellungen liegen nur dort; *Konfigurieren* in Geräte & Dienste führt ebenfalls dorthin.

Mit der ersten Tür trägt Portier selbst eine Zeile in die `configuration.yaml` ein (vorher wird `configuration.yaml.portier-backup` angelegt, danach wird die Datei geprüft):

```yaml
frontend:
  extra_module_url:
    - /local/portier/loader.js
```

Damit lädt HA Portier bei jedem Start ab der ersten Sekunde auf jeder Seite, auch nach einem Stromausfall oder wenn die App sich neu verbindet. Ein Neustart dafür ist nicht nötig: Die Zeile wird beim nächsten Start ohnehin gelesen. Ist die Datei ungewöhnlich aufgebaut (z. B. `frontend:` über `!include`), lässt Portier sie unverändert und sagt unter *Einstellungen → Reparaturen*, was von Hand einzutragen ist.

**Einmalig beim Ersteinrichten:** Geräte, auf denen Home Assistant schon vor dem Anlegen der ersten Tür offen war (z. B. ein Wand-Tablet), die Seite einmal neu laden; die App einmal ganz schließen und wieder öffnen. Danach nie wieder. Push und Telegram funktionieren sofort.

**Entfernen:** Wird die letzte Portier-Tür gelöscht, nimmt Portier die Zeile wieder aus der `configuration.yaml` (mit Sicherung) und löscht seinen Lader. Wird später wieder eine Tür angelegt, trägt Portier alles erneut ein.

## Türen verwalten

Alles geht direkt in der Seitenleiste **Türklingel** (als Administrator):

- **+ Tür hinzufügen** oben rechts legt eine weitere Tür an.
- Das **Zahnrad** an einer Tür öffnet alle Einstellungen: Geräte, Push und Telegram, Klingelton (mit „Anhören“), Lautstärke, Zeiten, Türöffner-Impuls. Unten lässt sich die Tür auch entfernen.

*Konfigurieren* unter *Geräte & Dienste → Portier* führt direkt zu diesem Editor – es gibt nur einen Ort für alle Einstellungen.

## Benachrichtigungen

**Handy (Home-Assistant-App):** Unter „Push-Benachrichtigungen“ entweder **„Alle Handys mit der App“** wählen – dann bekommt jedes Handy mit der App die Nachricht, auch eins, das später dazukommt – oder einzelne Handys (`mobile_app_…`) ankreuzen. Die Nachricht zeigt das Kamerabild und den Knopf **Tür öffnen** (das Handy verlangt dafür die Entsperrung). Wer auf die Nachricht tippt, landet direkt im Livebild dieser Tür, mit dem Türöffner im Bild.

**Telegram:** Die Telegram-Bot-Integration muss eingerichtet sein. Die Empfänger per Haken auswählen – angeboten werden die Chats, die in der Telegram-Bot-Integration erlaubt sind (ohne solche Chats: Chat-IDs kommagetrennt eintragen). Es kommt ein Foto mit **Tür öffnen**-Knopf. Nur die eingetragenen Chats dürfen damit öffnen.

**Andere Dienste** (E-Mail, Alexa, …) bekommen eine Textnachricht.

„notify“ ist in Home Assistant der Sammelbegriff für alle Wege, eine Nachricht zu verschicken. Jedes Handy mit der App bringt einen eigenen Dienst mit (`notify.mobile_app_<handy>`). Ein Handy bekommt jede Nachricht nur einmal, auch wenn es zusätzlich einzeln angekreuzt ist.

Jeder Öffnen-Knopf funktioniert nur **einmal** und nur kurz nach der Nachricht (Standard 120 Sekunden, einstellbar 1–120 s). In Telegram verschwindet der Knopf danach, und sobald die Tür geöffnet wurde – egal wie –, steht dort „Tür geöffnet“. Ein alter Knopf von gestern öffnet nichts mehr. Wer mehrmals klingelt, löst nicht jedes Mal eine Nachricht aus (Mindestabstand einstellbar).

Wenn du bisher eigene Automationen für Push oder Telegram hast, deaktiviere sie, sonst kommen Nachrichten doppelt.

## Öffnen per NFC-Aufkleber

Wer ein Handy mit der Home-Assistant-App hat, kann die Tür öffnen, indem er es an einen NFC-Aufkleber neben der Tür hält – ohne Tippen, praktisch für Kinder.

1. Aufkleber anbringen und das Handy einmal dranhalten (oder unter *Einstellungen → Tags* anlegen). Home Assistant kennt ihn dann.
2. Beim **Zahnrad** der Tür unter **„Wer darf öffnen“** den Schalter **„Öffnen per NFC-Aufkleber“** einschalten, dann den Aufkleber und die **Handys, die öffnen dürfen**, ankreuzen. Andere Handys am Aufkleber bewirken nichts.
3. Optional: Zeitfenster (z. B. 07:00–20:00) und eine Nachricht an die Push- und Telegram-Empfänger, wenn jemand per NFC öffnet.

Das Handy muss Home Assistant dabei erreichen – zu Hause also WLAN bis an die Tür, unterwegs ein Zugang von außen (z. B. Home Assistant Cloud). In der App unter *Begleit-App → Server* das Heim-WLAN eintragen. Jedes Öffnen steht im Türöffner-Verlauf.

## Türöffner sicher betreiben

Bei Schaltern schaltet Portier nach der Impulsdauer (Standard 3 s) wieder aus. Die Abschaltung wird vorher gespeichert. Startet Home Assistant mitten im Impuls neu, schaltet Portier das Relais beim Start sofort aus. Klappt das Ausschalten nicht, versucht Portier es weiter und zeigt eine Benachrichtigung in HA.

**Am sichersten:** Im Relais selbst einen Auto-Aus-Timer einstellen (bei Shelly „Auto Off“) und in Portier „Gerät (eigener Auto-Aus-Timer)“ wählen. Dann bleibt die Tür auch bei Stromausfall oder WLAN-Abbruch nicht offen.

Bei Schlössern nutzt Portier „öffnen“ (Falle), wenn das Schloss das kann, sonst „entriegeln“.

## Bedienung

- **Es klingelt:** Ton und Livebild erscheinen. Die Messinglinie unten im Bild zeigt, wie lange das Bild noch offen bleibt (Standard 60 s).
- **Öffnen:** Messing-Taster antippen. Er dreht kurz und wird grün, sobald die Tür offen ist. Das Bild bleibt danach noch 20 s offen.
- **Schließen:** Wer ein Klingel-Livebild mit × schließt, schließt es auf allen Geräten. Eine von Hand geöffnete Kamera schließt nur auf dem eigenen Gerät. Klingelt es inzwischen erneut, bleibt das neue Bild offen.
- **Mehrere Türen gleichzeitig:** Im Querformat stehen die Livebilder nebeneinander und werden kleiner, je mehr es sind. Hochkant stehen sie untereinander. Das zuletzt klingelnde ist vorne und golden umrandet.
- **Ton:** Wird beim ersten Antippen der Seite automatisch freigeschaltet (Browser-Regel). Ist er blockiert, steht das im Bild.
- **Verlauf:** Unter jeder Tür lassen sich zwei Listen aufklappen. Der **Klingelverlauf** zeigt, welcher Taster geklingelt hat (oder wer die Testklingel gedrückt hat), der **Türöffner-Verlauf**, wer wann geöffnet hat und wie – App (auch der Knopf in der Push-Nachricht), Browser, Telegram, NFC, Taste oder Automation. Dasselbe steht nach dem Öffnen im Livebild, z. B. „Tür geöffnet · von phil über App“. Beide Verläufe bleiben auch nach einem Neustart erhalten (je die letzten 50).
- **Seitenleiste „Türklingel“:** Standbild, Live ansehen, Testklingel, Verlauf und die Einstellungen für dieses Gerät (Livebild und Ton an/aus pro Browser, z. B. das Tablet im Schlafzimmer stumm).

## Für eigene Automationen

| Entität / Ereignis | Wozu |
| --- | --- |
| `event.<tür>_klingel` | Klingel-Ereignis (Gerätetyp Türklingel) |
| `button.<tür>_tur_offnen` | Tür öffnen |
| `button.<tür>_testklingel` | Testklingel |
| `sensor.<tür>_zuletzt_geklingelt` / `_zuletzt_geoffnet` | Zeitstempel |
| Ereignisse `portier_ring`, `portier_opened` (mit `via` und `user`), `portier_dismissed` | für Automationen |
| Aktionen `portier.ring`, `portier.open`, `portier.show` | auslösen, öffnen, Livebild zeigen |

## Rechte

Standardmäßig darf jeder angemeldete Benutzer öffnen, der die Kamera sehen darf (z. B. das Familien-Tablet). Unter „Türöffner & Gong“ kannst du das auf Administratoren beschränken.

## Grenzen

- Das schwebende Livebild erscheint nur in Home-Assistant-Seiten, die beim Klingeln offen und sichtbar sind. Wer die App erst danach öffnet, bekommt kein verspätetes Livebild – dafür sind die Push-Nachrichten da.
- Das Livebild kommt per WebRTC über Home Assistant (z. B. go2rtc, das in HA eingebaut ist). Geht das nicht, nimmt Portier HLS oder MJPEG. Bis das Video läuft, ist das letzte Standbild zu sehen.
- Gegensprechen (2-Wege-Audio) ist noch nicht eingebaut. Das ist mit go2rtc und WebRTC möglich und als nächster Schritt vorgesehen.

## Tests

```sh
pip install pytest-homeassistant-custom-component
pytest
```
