"""Constants, topic helpers and pure device<->HA logic for Faikout.

This module MUST NOT import `homeassistant` — it is unit-tested standalone.
It uses the plain string values of HA's HVACMode / HVACAction StrEnums;
entity modules wrap them in the real enums.
"""
from __future__ import annotations

import json

DOMAIN = "faikout"

# Bounds on what a device may push into long-lived state. Anything on the state
# topics is untrusted: the module is an ESP32 on the LAN, and on a broker without
# per-topic ACLs any client can publish there. Legitimate status frames are well
# under a kilobyte and carry a few dozen fields.
MAX_PAYLOAD_CHARS = 16384
MAX_STATE_FIELDS = 256
MAX_META_TEXT = 64
PLATFORMS = ["climate", "number", "sensor", "switch"]
CONF_HOST = "host"
# Stable per-module identity (MAC when known, hostname otherwise). Everything
# HA keys on — config entry, device, entity unique ids — uses this, never the
# hostname directly.
CONF_DEVICE_ID = "device_id"
CONF_MAC = "mac"
DISCOVERY_TOPIC = "state/+"

# Option: throttle how often incoming MQTT updates are pushed into HA entities.
# 0 = real-time (every message). N>0 = at most one update per N seconds
# (the latest value is always flushed). Purely HA-side; does not touch the device.
CONF_UPDATE_INTERVAL = "update_interval"
# Default to coalescing updates into one push per 10s. The module reports on
# every change, which for a running AC is far more often than anyone needs and
# writes a row to the recorder each time. Set 0 for every message.
DEFAULT_UPDATE_INTERVAL = 10

# Option: use an own MQTT client (connect directly to a broker) instead of the
# shared Home Assistant MQTT integration. Useful when the Faikout lives on a
# different broker than HA's MQTT client.
CONF_USE_OWN_MQTT = "use_own_mqtt"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_PORT = "mqtt_port"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
# Encrypt the connection to an own broker. Off by default, because a broker on
# the LAN commonly has no usable certificate.
CONF_MQTT_TLS = "mqtt_tls"
# Accept any certificate. Needed for the self-signed certificate brokers ship
# with (EMQX's demo certificate says CN=localhost and is signed by nobody you
# trust), at the cost of not being able to detect an impersonated broker.
CONF_MQTT_TLS_INSECURE = "mqtt_tls_insecure"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TLS_PORT = 8883


def effective_port(port, tls: bool) -> int:
    """Port to actually use, moving to 8883 when TLS is switched on.

    The port field keeps its plaintext default of 1883 when a user ticks TLS,
    which would then just fail to connect.

    Note the limit of this: 1883 typed deliberately is indistinguishable from
    1883 left untouched, so TLS on 1883 specifically cannot be configured. That
    combination is vanishingly rare — 1883 is the registered plaintext port —
    and the alternative, silently failing to connect for everyone who ticks TLS
    without touching the port, is far worse. Every other port is honoured.
    """
    port = int(port)
    if tls and port == DEFAULT_MQTT_PORT:
        return DEFAULT_MQTT_TLS_PORT
    return port


def normalize_mac(mac) -> str | None:
    """Lowercase colon-separated MAC, or None if it is not one."""
    if not mac:
        return None
    cleaned = "".join(c for c in str(mac).lower() if c in "0123456789abcdef")
    if len(cleaned) != 12:
        return None
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def device_id_for(mac, host: str) -> str:
    """Stable identity for one module: its MAC when known, else the hostname.

    A hostname is only unique within a single broker, so two modules with the
    same name on different brokers would otherwise look like one device. The
    MAC comes from the bare state topic during discovery; a hand-typed hostname
    has none, which is why the fallback exists.
    """
    return normalize_mac(mac) or host


def is_valid_host(host: str) -> bool:
    """Whether a hostname is safe to build MQTT topics from.

    The host is substituted straight into the topic, so the MQTT wildcards
    ``+`` and ``#`` and the level separator ``/`` must be rejected: they would
    turn a publish into an invalid topic and a subscribe into a wildcard that
    matches other devices. Whitespace and control characters are refused too.
    """
    if not host or host.strip() != host:
        return False
    if any(c in host for c in "+#/"):
        return False
    return all(c.isprintable() and not c.isspace() for c in host)


def state_topic(host: str) -> str:
    return f"state/{host}"


def status_topic(host: str) -> str:
    # The device publishes the protocol-format status (mode "C", fan "A",
    # swingv/swingh, home=current, temp=setpoint) under the /status suffix.
    # The bare state/<host> topic carries a different word-format app status.
    return f"state/{host}/status"


def control_topic(host: str) -> str:
    return f"command/{host}/control"


# --- HVAC mode (strings match HVACMode StrEnum values) ----------------------
HVAC_OFF = "off"
HVAC_HEAT = "heat"
HVAC_COOL = "cool"
HVAC_AUTO = "auto"
HVAC_DRY = "dry"
HVAC_FAN_ONLY = "fan_only"
HVAC_MODES = [HVAC_OFF, HVAC_HEAT, HVAC_COOL, HVAC_AUTO, HVAC_DRY, HVAC_FAN_ONLY]

MODE_DEV_TO_HA = {
    "H": HVAC_HEAT,
    "C": HVAC_COOL,
    "A": HVAC_AUTO,
    "D": HVAC_DRY,
    "F": HVAC_FAN_ONLY,
}
MODE_HA_TO_DEV = {v: k for k, v in MODE_DEV_TO_HA.items()}

# --- HVAC action (strings match HVACAction values) --------------------------
ACTION_OFF = "off"
ACTION_HEATING = "heating"
ACTION_COOLING = "cooling"
ACTION_DRYING = "drying"
ACTION_IDLE = "idle"
ACTION_FAN = "fan"

# --- Fan --------------------------------------------------------------------
# The device reports "quiet" as a separate boolean flag (see SWITCH_FIELDS),
# NOT as a fan value. Fan levels are auto + manual 1-5.
FAN_AUTO = "auto"
FAN_MODES = [FAN_AUTO, "1", "2", "3", "4", "5"]


def fan_dev_to_ha(value) -> str | None:
    if value is None:
        return None
    s = str(value).upper()
    if s in ("A", "Q"):  # "Q" (legacy quiet-as-fan) maps to auto
        return FAN_AUTO
    return str(value)


def fan_ha_to_dev(mode: str):
    if mode == FAN_AUTO:
        return "A"
    return int(mode)


# --- Swing ------------------------------------------------------------------
SWING_OFF = "off"
SWING_VERTICAL = "vertical"
SWING_HORIZONTAL = "horizontal"
SWING_BOTH = "both"
SWING_MODES = [SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH]


def swing_dev_to_ha(swingv, swingh) -> str:
    v, h = bool(swingv), bool(swingh)
    if v and h:
        return SWING_BOTH
    if v:
        return SWING_VERTICAL
    if h:
        return SWING_HORIZONTAL
    return SWING_OFF


def swing_ha_to_dev(mode: str) -> dict:
    return {
        "swingv": mode in (SWING_VERTICAL, SWING_BOTH),
        "swingh": mode in (SWING_HORIZONTAL, SWING_BOTH),
    }


# --- Demand -----------------------------------------------------------------
# Output limit in percent. The device refuses anything below 30 (verified live
# against the firmware's own control, which offers 30..100), so a plain 0-100
# range would silently accept values that never take effect.
DEMAND_MIN = 30
DEMAND_MAX = 100
DEMAND_STEP = 5


def build_demand_command(value) -> dict:
    return {"demand": int(value)}


# --- Temperature / entity sets ----------------------------------------------
TEMP_MIN = 16.0
TEMP_MAX = 32.0  # firmware HA discovery reports max_temp 32
TEMP_STEP = 0.5
TEMP_SENSORS = ["home", "outside", "inlet", "liquid"]
SWITCH_FIELDS = [
    "powerful",
    "econo",
    "streamer",
    "quiet",
    "comfort",
    "sensor",
    "led",
    "swingv",
    "swingh",
]


# --- Device metadata --------------------------------------------------------
def device_metadata(meta: dict) -> dict:
    """Extract HA device fields from the bare ``state/<host>`` app status.

    That topic carries ``app`` (product), ``version`` (firmware), ``build-suffix``
    (hardware variant) and ``id`` (MAC). ``/status`` does not, so device info is
    sourced from here. Missing keys yield ``None``.
    """
    def _text(value):
        # These end up in the persisted device registry, so they are truncated
        # rather than trusted at whatever length the device sent.
        if value is None:
            return ""
        return str(value)[:MAX_META_TEXT]

    app = _text(meta.get("app")) or "Faikout"
    suffix = _text(meta.get("build-suffix")).lstrip("-").strip()
    model = f"{app} {suffix}".strip() if suffix else app
    return {
        "model": model or None,
        "sw_version": _text(meta.get("version")) or None,
        # Normalised here too, not just at config-entry creation: this value
        # reaches the device registry and format_mac on every metadata change.
        "mac": normalize_mac(meta.get("id")),
    }


def parse_device_meta(payload) -> dict | None:
    """Parse the bare ``state/<host>`` payload under the same bounds as status.

    This topic is just as untrusted as the status one, and the result is kept
    on the coordinator and written into the device registry.
    """
    if payload is None or len(payload) > MAX_PAYLOAD_CHARS:
        return None
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if len(parsed) > MAX_STATE_FIELDS:
        return dict(list(parsed.items())[:MAX_STATE_FIELDS])
    return parsed


# --- State readers ----------------------------------------------------------
def hvac_mode_from_state(data: dict) -> str | None:
    if not data.get("power", False):
        return HVAC_OFF
    mode = data.get("mode")
    return MODE_DEV_TO_HA.get(mode) if isinstance(mode, str) else None


def hvac_action_from_state(data: dict) -> str:
    if not data.get("power", False):
        return ACTION_OFF
    if data.get("heat"):
        return ACTION_HEATING
    mode = data.get("mode")
    if mode == "C":
        return ACTION_COOLING
    if mode == "D":
        return ACTION_DRYING
    if mode == "F":
        return ACTION_FAN
    return ACTION_IDLE


# --- Command builders -------------------------------------------------------
def build_hvac_mode_command(ha_mode: str) -> dict:
    if ha_mode == HVAC_OFF:
        return {"power": False}
    return {"power": True, "mode": MODE_HA_TO_DEV[ha_mode]}


def build_temperature_command(temp) -> dict:
    return {"temp": temp}


def build_fan_command(ha_fan: str) -> dict:
    return {"fan": fan_ha_to_dev(ha_fan)}


def build_swing_command(ha_swing: str) -> dict:
    return swing_ha_to_dev(ha_swing)


def build_switch_command(field: str, on: bool) -> dict:
    return {field: bool(on)}


# --- State merge (pure) -----------------------------------------------------
def merge_state(current: dict | None, payload: str) -> dict | None:
    """Merge a raw MQTT payload into the current state dict.

    Returns the new dict, or None if unparseable/ignored. A bare
    'true'/'false' is the module presence (LWT/birth).
    """
    data = dict(current or {})
    if payload in ("true", "false"):
        data["online"] = payload == "true"
        return data
    if payload is None or len(payload) > MAX_PAYLOAD_CHARS:
        # Refuse oversized payloads before parsing: a huge object of distinct
        # keys costs real time on the event loop and would be merged in below.
        return None
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    for key, value in parsed.items():
        # Known fields always update; new ones only until the cap. Without this
        # a device streaming fresh key names grows this dict without limit for
        # the lifetime of the config entry.
        if key in data or len(data) < MAX_STATE_FIELDS:
            data[key] = value
    return data
