# ☀️ Balcony Solar Monitoring with OpenDTU, MQTT & Grafana

A complete monitoring stack for a balcony power plant (Hoymiles HM-800) — from data collection at the inverter to visualization in Grafana. The entire stack was set up with the help of the AI agent [OpenClaw](https://openclaw.ai) in just a few steps, including automatic LXC container creation on Proxmox, configuration, and deployment.

---

## 🏗️ Architecture

```
[Hoymiles HM-800]
      |
      | (RF 868 MHz)
      v
[OpenDTU @ ESP32]  ──── MQTT ────►  [Mosquitto Broker]  (Home LAN)
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

**Two zones:**
- **Home LAN**: OpenDTU (ESP32), Mosquitto broker, mqtt-bridge LXC
- **Hetzner server (internet)**: PHP proxy, InfluxDB v2, Grafana — all as LXC containers on Proxmox

The separation has an important security benefit: InfluxDB is not directly reachable from the internet. Only the PHP proxy is exposed, acting as a minimal firewall.

---

## 🔧 Components

### 1. Hoymiles HM-800 + OpenDTU

- **Hardware**: Hoymiles HM-800 micro inverter with two solar panels (SE = South-East, SW = South-West)
- **OpenDTU**: Runs on an ESP32, reads the inverter via RF and publishes all measurements via MQTT
- **MQTT topics**: `solar/<serial>/#` — includes AC power, DC power per panel, voltage, temperature, efficiency, irradiation, production status, and more

### 2. Mosquitto MQTT Broker (Home LAN)

Standard installation, listening on the local network. No external exposure needed.

```bash
apt install mosquitto mosquitto-clients
```

### 3. mqtt-bridge (LXC in Home LAN)

Python service that subscribes to MQTT topics and forwards data in batches via HTTP to the Hetzner server.

**Key features:**
- Subscribes to `solar/<serial>/#`
- Converts measurements to **InfluxDB line protocol**
- Buffers data points and flushes them at configurable intervals (default: 30 s)
- **Smart `producing` filter**: When the inverter is not producing (night, heavy cloud cover), no data points are buffered — saves storage and avoids zero-value spam
- Retry logic with exponential backoff on POST failures
- `SIGUSR1` signal for live statistics without restart
- Configuration via YAML (`/etc/mqtt-bridge/mqtt-bridge.yaml`)

**Example configuration (`mqtt-bridge.yaml`):**

```yaml
broker: 192.168.1.x         # IP of the Mosquitto broker
port: 1883
topic: solar/114180xxxxxx/# # OpenDTU MQTT topic (replace with your serial number)
measurement: mqtt_consumer   # InfluxDB measurement name
post_url: https://example.com/influx.php
post_interval: 30            # Flush interval in seconds
post_timeout: 10
post_retries: 2
keepalive: 60
verbose: false
```

**Install as a systemd service:**

```bash
pip3 install paho-mqtt requests pyyaml
cp mqtt-bridge.py /usr/local/bin/
cp mqtt-bridge.yaml /etc/mqtt-bridge/
```

```ini
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

### 4. influx.php (Hetzner Server)

Minimal PHP proxy that forwards incoming HTTP POSTs in InfluxDB line protocol to the local InfluxDB instance.

**Why PHP?** No framework, no overhead — a single file that runs on any standard web server.

**Security note for production:** It is recommended to add a shared API key as an HTTP header (`X-Api-Key`) and/or restrict access via IP allowlist to your home network's IP.

```php
define('INFLUX_URL',    'http://10.1.5.21:8086/api/v2/write');
define('INFLUX_ORG',    'solar');
define('INFLUX_BUCKET', 'solar');
define('INFLUX_TOKEN',  'your-influxdb-token');
```

### 5. InfluxDB v2 (LXC on Hetzner)

- Organization: `solar`
- Bucket: `solar`
- Measurement: `mqtt_consumer`
- Tag: `topic` (contains the full MQTT topic path)
- Field: `value` (measurement value as string)

**Install InfluxDB v2:**

```bash
# Debian/Ubuntu
wget https://dl.influxdata.com/influxdb/releases/influxdb2_2.x.x_amd64.deb
dpkg -i influxdb2_2.x.x_amd64.deb
systemctl enable --now influxdb
```

### 6. Grafana (LXC on Hetzner)

The "Solaranlage Balkon" dashboard visualizes all relevant measurements in real time (refresh: 10 s).

**Panels:**

| Panel | Content |
|---|---|
| AC Power (W) | Total inverter output power |
| DC Power per Panel (W) | Individual power for SE and SW panels |
| Current Values | Stat panel with current total yield |
| Today's Yield | Daily yield in Wh |
| Total Yield | Cumulative total yield |
| AC Voltage (V) | Grid voltage |
| DC Voltage per Panel (V) | DC input voltage per module |
| Efficiency & Temperature | Efficiency and module temperature over time |
| DTU Status | Online/offline status of the ESP32 |
| DTU WiFi RSSI | WiFi signal strength as gauge |
| Irradiation (%) | Relative irradiation per panel |

**Practical observation:** The two different panel orientations (SE and SW) complement each other perfectly — the south-east panel reaches peak output earlier in the day, while the south-west panel continues producing well into the afternoon and evening. This results in a significantly wider yield window compared to a pure south-facing setup.

---

## 📊 Data Flow in Detail

```
OpenDTU publishes e.g.:
  Topic:   solar/114180xxxxxx/ch0/power
  Payload: 423.5

mqtt-bridge converts to InfluxDB line protocol:
  mqtt_consumer,topic=solar/114180xxxxxx/ch0/power value="423.5"

influx.php forwards to:
  POST http://influxdb:8086/api/v2/write?org=solar&bucket=solar&precision=ns

Grafana query (Flux):
  from(bucket: "solar")
    |> range(start: v.timeRangeStart)
    |> filter(fn: (r) => r.topic == "solar/114180xxxxxx/ch0/power")
    |> toFloat()
```

---

## 🚀 Deployment Workflow (How It Was Built)

The entire stack was built using [OpenClaw](https://openclaw.ai) as an AI assistant:

1. Described the architecture in chat (OpenDTU → MQTT → Bridge → InfluxDB → Grafana)
2. OpenClaw automatically created LXC containers on a test Proxmox server
3. Installed all dependencies, wrote configuration files and systemd units
4. Tested the data pipeline end-to-end
5. Created the Grafana dashboard including all panels and queries

The finished LXC container was then migrated to the production Proxmox server.

---

## 📋 Prerequisites

- Hoymiles micro inverter (HM series)
- ESP32 with [OpenDTU](https://github.com/tbnobody/OpenDTU) firmware
- Proxmox server (home LAN + optional Hetzner/cloud)
- Python 3.9+
- PHP 7.4+ with `allow_url_fopen = On`
- InfluxDB v2
- Grafana 9+

---

## 🔗 Links

- [OpenDTU Project](https://github.com/tbnobody/OpenDTU)
- [OpenClaw AI Agent](https://openclaw.ai)
- [InfluxDB v2 Documentation](https://docs.influxdata.com/influxdb/v2/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Hoymiles HM-800](https://www.hoymiles.com)

---

*Built with the help of [OpenClaw](https://openclaw.ai) & Claude (Anthropic) 🦞*
