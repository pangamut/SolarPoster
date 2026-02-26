# SolarPoster
Publish OpenDTU data to Grafana

# ☀️ Balkonkraftwerk Monitoring mit OpenDTU, MQTT & Grafana

Dieses Projekt beschreibt einen vollständigen Monitoring-Stack für ein Balkonkraftwerk (Hoymiles HM-800) – von der Datenerfassung am Wechselrichter bis zur Visualisierung in Grafana. Der Stack entstand mit Unterstützung des KI-Agenten [OpenClaw](https://openclaw.ai) in wenigen Schritten, inklusive automatischer LXC-Erstellung auf Proxmox, Konfiguration und Deployment.

---

## 🏗️ Architektur

```
[Hoymiles HM-800]
      |
      | (RF 868 MHz)
      v
[OpenDTU @ ESP32]  ──── MQTT ────►  [Mosquitto Broker]  (Heimnetz)
                                            |
                                            | MQTT Subscribe
                                            v
                                   [mqtt-bridge @ LXC]
                                            |
                                            | HTTP POST (Line Protocol)
                                            v
                                   [influx.php @ Hetzner]
                                            |
                                            | native InfluxDB API
                                            v
                                    [InfluxDB v2 @ LXC]
                                            |
                                            v
                                     [Grafana @ LXC]
```

**Zwei Zonen:**
- **Heimnetz**: OpenDTU (ESP32), Mosquitto-Broker, mqtt-bridge-LXC
- **Hetzner-Server (Internet)**: PHP-Proxy, InfluxDB v2, Grafana – alle als LXC-Container auf Proxmox

Die Trennung hat einen wichtigen Sicherheitsaspekt: InfluxDB ist nicht direkt aus dem Internet erreichbar. Nur der PHP-Proxy ist exponiert, der als minimale Firewall fungiert.

---

## 🔧 Komponenten

### 1. Hoymiles HM-800 + OpenDTU

- **Hardware**: Hoymiles HM-800 Micro-Wechselrichter mit zwei Solarmodulen (SO Südost, SW Südwest)
- **OpenDTU**: Läuft auf einem ESP32, liest den Wechselrichter per RF aus und publisht alle Messwerte per MQTT
- **MQTT-Topics**: `solar/<SerienNr>/#` – enthält AC-Leistung, DC-Leistung pro Panel, Spannung, Temperatur, Wirkungsgrad, Einstrahlungsstärke, Produktionsstatus u.v.m.

### 2. Mosquitto MQTT-Broker (Heimnetz)

Standard-Installation, lauscht im lokalen Netz. Keine externe Exposition nötig.

```bash
apt install mosquitto mosquitto-clients
```

### 3. mqtt-bridge (LXC im Heimnetz)

Python-Dienst, der MQTT-Topics abonniert und die Daten gebündelt per HTTP an den Hetzner-Server weiterleitet.

**Kernfunktionen:**
- Subscribed auf `solar/<SerienNr>/#`
- Konvertiert Messwerte in **InfluxDB Line Protocol**
- Puffert Datenpunkte und flusht sie in konfigurierbaren Intervallen (Standard: 30 s)
- **Intelligenter `producing`-Filter**: Wenn der Wechselrichter nicht produziert (Nacht, starke Bewölkung), werden keine Datenpunkte gepuffert – spart Speicher und vermeidet Nullwert-Spam
- Retry-Logik mit exponentiellem Backoff bei POST-Fehlern
- `SIGUSR1`-Signal für Live-Statistiken ohne Neustart
- Konfiguration via YAML (`/etc/mqtt-bridge/mqtt-bridge.yaml`)

**Beispiel-Konfiguration (`mqtt-bridge.yaml`):**

```yaml
broker: 192.168.1.x        # IP des Mosquitto-Brokers
port: 1883
topic: solar/114180xxxxxx/# # OpenDTU MQTT-Topic (Seriennummer anpassen)
measurement: mqtt_consumer  # InfluxDB Measurement-Name
post_url: https://example.com/influx.php
post_interval: 30           # Flush-Intervall in Sekunden
post_timeout: 10
post_retries: 2
keepalive: 60
verbose: false
```

**Installation als systemd-Dienst:**

```bash
pip3 install paho-mqtt requests pyyaml
cp mqtt-bridge.py /usr/local/bin/
cp mqtt-bridge.yaml /etc/mqtt-bridge/

# /etc/systemd/system/mqtt-bridge.service
[Unit]
Description=MQTT to InfluxDB Bridge
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/mqtt-bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now mqtt-bridge
```

### 4. influx.php (Hetzner-Server)

Minimaler PHP-Proxy, der eingehende HTTP-POSTs im InfluxDB Line Protocol an die lokale InfluxDB-Instanz weiterleitet.

**Warum PHP?** Kein Framework, kein Overhead – ein einzelnes File, das auf jedem Standard-Webserver läuft.

**Sicherheitshinweis für Produktion:** Es empfiehlt sich, einen gemeinsamen API-Key als HTTP-Header (`X-Api-Key`) zu ergänzen und/oder den Zugriff per IP-Whitelist auf die Heimnetz-IP zu beschränken.

```php
define('INFLUX_URL',    'http://10.1.5.21:8086/api/v2/write');
define('INFLUX_ORG',    'solar');
define('INFLUX_BUCKET', 'solar');
define('INFLUX_TOKEN',  'dein-influxdb-token');
```

### 5. InfluxDB v2 (LXC auf Hetzner)

- Organisation: `solar`
- Bucket: `solar`
- Measurement: `mqtt_consumer`
- Tag: `topic` (enthält den vollen MQTT-Topic-Pfad)
- Field: `value` (Messwert als String)

**InfluxDB v2 installieren:**

```bash
# Debian/Ubuntu
wget https://dl.influxdata.com/influxdb/releases/influxdb2_2.x.x_amd64.deb
dpkg -i influxdb2_2.x.x_amd64.deb
systemctl enable --now influxdb
```

### 6. Grafana (LXC auf Hetzner)

Das Dashboard „Solaranlage Balkon" visualisiert alle relevanten Messwerte in Echtzeit (Refresh: 10 s).

**Panels:**

| Panel | Inhalt |
|---|---|
| AC Leistung (W) | Gesamtleistung des Wechselrichters |
| DC Leistung pro Panel (W) | Einzelleistung SO (Südost) & SW (Südwest) |
| Aktuelle Werte | Stat-Panel mit aktuellem Gesamtertrag |
| Ertrag heute | Tagesertrag in Wh |
| Ertrag gesamt | Kumulierter Gesamtertrag |
| Spannung AC (V) | Netzspannung |
| Spannung DC pro Panel (V) | DC-Eingangsspannung je Modul |
| Wirkungsgrad & Temperatur | Effizienz und Modultemperatur im Zeitverlauf |
| DTU Status | Online/Offline-Status des ESP32 |
| DTU WiFi RSSI | WLAN-Signalstärke als Gauge |
| Einstrahlung / Irradiation (%) | Relative Einstrahlungsstärke je Panel |

**Interessante Beobachtung aus der Praxis:** Die zwei unterschiedlichen Panel-Ausrichtungen (SO und SW) ergänzen sich ideal – das Südost-Panel liefert früher am Tag mehr Leistung, das Südwest-Panel produziert länger in den Nachmittag und Abend hinein. Das ergibt eine deutlich breitere Ertragsperiode als bei reiner Südausrichtung.

---

## 📊 Datenfluss im Detail

```
OpenDTU publisht z.B.:
  Topic:   solar/114180xxxxxx/ch0/power
  Payload: 423.5

mqtt-bridge konvertiert zu InfluxDB Line Protocol:
  mqtt_consumer,topic=solar/114180xxxxxx/ch0/power value="423.5"

influx.php leitet weiter an:
  POST http://influxdb:8086/api/v2/write?org=solar&bucket=solar&precision=ns

Grafana-Query (Flux):
  from(bucket: "solar")
    |> range(start: v.timeRangeStart)
    |> filter(fn: (r) => r.topic == "solar/114180xxxxxx/ch0/power")
    |> toFloat()
```

---

## 🚀 Deployment-Workflow (wie es entstanden ist)

Der gesamte Stack wurde mit [OpenClaw](https://openclaw.ai) als KI-Assistent aufgebaut:

1. Architektur im Chat beschrieben (OpenDTU → MQTT → Bridge → InfluxDB → Grafana)
2. OpenClaw erstellte automatisch LXC-Container auf einem Test-Proxmox-Server
3. Installierte alle Abhängigkeiten, schrieb Konfigurationsdateien und systemd-Units
4. Testete die Datenpipeline end-to-end
5. Erstellte das Grafana-Dashboard inkl. aller Panels und Queries

Der fertige LXC-Container wurde anschließend auf den Produktions-Proxmox-Server migriert.

---

## 📋 Voraussetzungen

- Hoymiles Micro-Wechselrichter (HM-Serie)
- ESP32 mit [OpenDTU](https://github.com/tbnobody/OpenDTU) Firmware
- Proxmox-Server (Heimnetz + optional Hetzner/Cloud)
- Python 3.9+
- PHP 7.4+ mit `allow_url_fopen = On`
- InfluxDB v2
- Grafana 9+

---

## 🔗 Weiterführende Links

- [OpenDTU Projekt](https://github.com/tbnobody/OpenDTU)
- [OpenClaw KI-Assistent](https://openclaw.ai)
- [InfluxDB v2 Dokumentation](https://docs.influxdata.com/influxdb/v2/)
- [Grafana Dokumentation](https://grafana.com/docs/)
- [Hoymiles HM-800](https://www.hoymiles.com)

---

*Erstellt mit Unterstützung von [OpenClaw](https://openclaw.ai) & Claude (Anthropic) 🦞*
