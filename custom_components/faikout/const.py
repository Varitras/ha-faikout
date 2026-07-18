"""Constants, topic helpers and pure device<->HA logic for Faikout.

This module MUST NOT import `homeassistant` — it is unit-tested standalone.
It uses the plain string values of HA's HVACMode / HVACAction StrEnums;
entity modules wrap them in the real enums.
"""
from __future__ import annotations

import json

DOMAIN = "faikout"
PLATFORMS = ["climate", "sensor", "switch"]
CONF_HOST = "host"
DISCOVERY_TOPIC = "state/+"

# Option: throttle how often incoming MQTT updates are pushed into HA entities.
# 0 = real-time (every message). N>0 = at most one update per N seconds
# (the latest value is always flushed). Purely HA-side; does not touch the device.
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 0

# Option: use an own MQTT client (connect directly to a broker) instead of the
# shared Home Assistant MQTT integration. Useful when the Faikout lives on a
# different broker than HA's MQTT client.
CONF_USE_OWN_MQTT = "use_own_mqtt"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_PORT = "mqtt_port"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
DEFAULT_MQTT_PORT = 1883


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
    app = meta.get("app") or "Faikout"
    suffix = (meta.get("build-suffix") or "").lstrip("-").strip()
    model = f"{app} {suffix}".strip() if suffix else app
    return {
        "model": model or None,
        "sw_version": meta.get("version") or None,
        "mac": meta.get("id") or None,
    }


# --- State readers ----------------------------------------------------------
def hvac_mode_from_state(data: dict) -> str | None:
    if not data.get("power", False):
        return HVAC_OFF
    return MODE_DEV_TO_HA.get(data.get("mode"))


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
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    data.update(parsed)
    return data
