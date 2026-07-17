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
TEMP_MAX = 30.0
TEMP_STEP = 0.5
TEMP_SENSORS = ["home", "outside", "inlet", "liquid"]
SWITCH_FIELDS = ["powerful", "econo", "streamer", "quiet", "swingv", "swingh"]


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
