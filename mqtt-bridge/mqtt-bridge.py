#!/usr/bin/env python3
"""
mqtt-bridge.py — Subscribe to OpenDTU MQTT topics, convert to InfluxDB
line protocol and POST to a remote HTTP endpoint.

Config: /etc/mqtt-bridge/mqtt-bridge.yaml
"""

__version__ = "mqtt-bridge-2026-001"

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
import yaml

log = logging.getLogger("mqtt-bridge")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_SEARCH = [
    Path(__file__).parent / "mqtt-bridge.yaml",
    Path("/etc/mqtt-bridge/mqtt-bridge.yaml"),
]


class Config:
    def __init__(self, data: dict):
        self.broker        = data.get("broker", "127.0.0.1")
        self.port          = int(data.get("port", 1883))
        self.topic         = data.get("topic", "solar/114180xxxxxx/#")
        self.measurement   = data.get("measurement", "mqtt_consumer")
        self.post_url      = data.get("post_url", "http://localhost/influx.php")
        self.post_interval = int(data.get("post_interval", 30))
        self.post_timeout  = int(data.get("post_timeout", 10))
        self.post_retries  = int(data.get("post_retries", 2))
        self.keepalive     = int(data.get("keepalive", 60))
        self.verbose       = bool(data.get("verbose", False))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        candidates = [path] if path else CONFIG_SEARCH
        for p in candidates:
            if p and p.exists():
                log.info("Loading config from %s", p)
                with open(p) as f:
                    return cls(yaml.safe_load(f) or {})
        log.info("No config file found, using defaults")
        return cls({})


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class MqttBridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._points: list[str] = []   # accumulated line protocol lines
        self._last_post = 0.0
        self._posts_ok = 0
        self._posts_err = 0
        self._producing = True          # assume producing until we know otherwise

        self._setup_mqtt()

    def _setup_mqtt(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

    # -- MQTT callbacks --

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
        else:
            log.info("Connected to MQTT broker %s:%d, subscribing to %s",
                     self.cfg.broker, self.cfg.port, self.cfg.topic)
            client.subscribe(self.cfg.topic)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT disconnected (%s), reconnecting...", reason_code)
        else:
            log.info("MQTT disconnected cleanly")

    def _on_message(self, client, userdata, message):
        """Convert MQTT message to InfluxDB line protocol and buffer it."""
        topic   = message.topic
        payload = message.payload.decode("utf-8", errors="replace").strip()

        if not payload:
            return

        # Track producing state — skip buffering when inverter is not producing
        if topic.endswith("/status/producing"):
            self._producing = payload != "0"
            log.debug("producing: %s", self._producing)

        if not self._producing:
            return

        # Escape tag/field values for line protocol
        # topic tag: escape spaces and commas
        topic_escaped = topic.replace(" ", "\\ ").replace(",", "\\,")

        # Always store as string (consistent with existing Telegraf/mqtt_consumer schema)
        escaped = payload.replace("\\", "\\\\").replace('"', '\\"')
        value_str = f'"{escaped}"'

        line = f'{self.cfg.measurement},topic={topic_escaped} value={value_str}'

        with self._lock:
            self._points.append(line)

        if self.cfg.verbose:
            log.debug("buffered: %s = %s", topic, payload)

    # -- Posting --

    def _flush(self):
        """Flush buffered points to the HTTP endpoint."""
        with self._lock:
            if not self._points:
                return
            batch = self._points.copy()
            self._points.clear()

        body = "\n".join(batch)
        log.debug("Flushing %d points to %s", len(batch), self.cfg.post_url)

        for attempt in range(1, self.cfg.post_retries + 1):
            try:
                r = requests.post(
                    self.cfg.post_url,
                    data=body.encode("utf-8"),
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                    timeout=self.cfg.post_timeout,
                )
                if 200 <= r.status_code < 300:
                    log.info("POST ok (%d) — %d points", r.status_code, len(batch))
                    self._posts_ok += 1
                    self._last_post = time.time()
                    return
                else:
                    log.warning("POST returned %d (attempt %d/%d): %s",
                                r.status_code, attempt, self.cfg.post_retries,
                                r.text[:100])
            except requests.RequestException as e:
                log.warning("POST failed (attempt %d/%d): %s",
                            attempt, self.cfg.post_retries, e)

            if attempt < self.cfg.post_retries:
                time.sleep(2 * attempt)

        log.error("All POST attempts failed — %d points dropped", len(batch))
        self._posts_err += 1

    def _print_stats(self):
        log.info("Stats: %d successful POSTs, %d errors, %d points buffered",
                 self._posts_ok, self._posts_err, len(self._points))

    # -- Main loop --

    def _connect_with_retry(self):
        """Connect to MQTT broker, retrying indefinitely with backoff."""
        delay = 5
        while True:
            try:
                self.client.connect(self.cfg.broker, self.cfg.port,
                                    keepalive=self.cfg.keepalive)
                return
            except (ConnectionRefusedError, OSError) as e:
                log.warning("MQTT connect failed (%s), retrying in %ds...", e, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)

    def run(self):
        log.info("%s starting (broker=%s, topic=%s, interval=%ds, url=%s)",
                 __version__, self.cfg.broker, self.cfg.topic,
                 self.cfg.post_interval, self.cfg.post_url)

        signal.signal(signal.SIGUSR1, lambda *_: self._print_stats())

        self._connect_with_retry()
        self.client.loop_start()

        try:
            while True:
                time.sleep(self.cfg.post_interval)
                self._flush()
        except KeyboardInterrupt:
            log.info("Shutting down...")
            self._flush()
            self._print_stats()
        finally:
            self.client.loop_stop()
            self.client.disconnect()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MQTT → InfluxDB line protocol bridge")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--config", type=Path, default=None)
    parser.add_argument("--broker", help="MQTT broker address")
    parser.add_argument("--url",    help="POST target URL")
    parser.add_argument("--interval", type=int, help="Flush interval in seconds")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stdout,
    )

    cfg = Config.load(args.config)
    if args.verbose:  cfg.verbose = True
    if args.broker:   cfg.broker  = args.broker
    if args.url:      cfg.post_url = args.url
    if args.interval: cfg.post_interval = args.interval

    MqttBridge(cfg).run()


if __name__ == "__main__":
    main()
