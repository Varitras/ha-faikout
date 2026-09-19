"""Constants, topic helpers and pure device<->HA logic for Faikout.

This module MUST NOT import `homeassistant` — it is unit-tested standalone.
It uses the plain string values of HA's HVACMode / HVACAction StrEnums;
entity modules wrap them in the real enums.
"""
from __future__ import annotations

import hashlib
import json
import math

DOMAIN = "faikout"

# Bounds on what a device may push into long-lived state. Anything on the state
# topics is untrusted: the module is an ESP32 on the LAN, and on a broker without
# per-topic ACLs any client can publish there. Legitimate status frames are well
# under a kilobyte and carry a few dozen fields.
MAX_PAYLOAD_CHARS = 16384
MAX_STATE_FIELDS = 256
MAX_META_TEXT = 64
# Discovery listens on a wildcard, so the host list is attacker-influenced too.
MAX_DISCOVERED_HOSTS = 64
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
# An action on the options form, never stored: the form cannot show the
# stored password to be deleted, so removing it needs a signal of its own.
CONF_MQTT_CLEAR_PASSWORD = "mqtt_clear_password"
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


def log_identifier(value) -> str:
    """A stand-in for a name that must not appear in a log record.

    Home Assistant logs are the usual attachment to a bug report, and a module
    host name or broker address says where someone lives. The digest is stable
    within an installation, so two lines about the same module can still be
    told apart without naming it.
    """
    text = str(value or "")
    if not text:
        return "<unset>"
    return "#" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


def error_kind(error: BaseException) -> str:
    """What went wrong, without what the library said about it.

    Library messages quote what they were given - a TLS failure spells out the
    hostname it rejected - so only the class and, where one exists, the
    machine-readable reason survive. The cause chain keeps the full text for
    anyone debugging with the exception in hand.
    """
    reason = getattr(error, "reason", None) or getattr(error, "errno", None)
    name = type(error).__name__
    return f"{name}({reason})" if reason else name


def masked_topic(topic) -> str:
    """The same, for a topic: the host is the middle segment and only that.

    Which topic a message arrived on is the useful half of such a log line, so
    the shape survives and only the name inside it is replaced.
    """
    parts = str(topic or "").split("/")
    if len(parts) < 2:
        return log_identifier(topic)
    parts[1] = log_identifier(parts[1])
    return "/".join(parts)


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
# The device decides between heating and cooling while the user still sets the
# target: that is Home Assistant's HEAT_COOL. Its AUTO means a schedule or
# learned behaviour sets the temperature and the user cannot - which would
# contradict the temperature control this entity offers. The firmware's own
# Home Assistant discovery publishes heat_cool for the same reason.
HVAC_HEAT_COOL = "heat_cool"
HVAC_DRY = "dry"
HVAC_FAN_ONLY = "fan_only"
HVAC_MODES = [HVAC_OFF, HVAC_HEAT, HVAC_COOL, HVAC_HEAT_COOL, HVAC_DRY, HVAC_FAN_ONLY]

MODE_DEV_TO_HA = {
    "H": HVAC_HEAT,
    "C": HVAC_COOL,
    "A": HVAC_HEAT_COOL,
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
ACTION_DEFROSTING = "defrosting"

# --- Fan --------------------------------------------------------------------
# Fan levels: auto ("A"), a quiet/night step ("Q"), and manual 1-5. The device
# fan value is a string; a numeric value is silently ignored (verified live:
# {"fan": 3} does nothing, {"fan": "3"} works). The separate "quiet" boolean
# flag (see SWITCH_FIELDS) is the OUTDOOR quiet setting, unrelated to this step.
FAN_AUTO = "auto"
FAN_QUIET = "quiet"
FAN_MODES = [FAN_AUTO, FAN_QUIET, "1", "2", "3", "4", "5"]
# CN_WIRED units only have three manual steps; the firmware maps them to 1/3/5
# and leaves 2 and 4 unused (get_fan_modes/fans_3_auto in the firmware source).
FAN_MODES_3 = [FAN_AUTO, FAN_QUIET, "1", "3", "5"]


def _protocol_key(protocol) -> str:
    """Normalise a reported protocol name for lookup.

    The firmware publishes the bare name plus an inversion marker when the
    line is inverted, e.g. "CN_WIRED¬Tx" or "S21¬Tx¬Rx", so
    everything from the marker on is dropped. Separator style is not
    guaranteed either, so hyphens and spaces fold to the underscore form.
    """
    name = str(protocol or "").split("¬")[0]
    return name.strip().upper().replace("-", "_").replace(" ", "_")


def fan_modes_for(protocol) -> list[str]:
    """Fan steps this unit actually has, from the reported protocol."""
    if _protocol_key(protocol) == "CN_WIRED":
        return FAN_MODES_3
    return FAN_MODES


# Temperature resolution per protocol (get_temp_step in the firmware). The
# names are the firmware's own spellings: prototype[] = { "S21", "X50A",
# "CN_WIRED", "Altherma_S" } - note X50A, not X50.
TEMP_STEP_BY_PROTOCOL = {
    "CN_WIRED": 1.0,
    "S21": 0.5,
    "X50A": 0.1,
    "ALTHERMA_S": 0.1,
}


def temp_step_for(protocol) -> float:
    """Setpoint resolution for the reported protocol, S21 default."""
    return TEMP_STEP_BY_PROTOCOL.get(_protocol_key(protocol), TEMP_STEP)


def fan_dev_to_ha(value) -> str | None:
    if value is None:
        return None
    s = str(value).upper()
    if s == "A":
        return FAN_AUTO
    if s == "Q":
        return FAN_QUIET
    return str(value)


def fan_ha_to_dev(mode: str) -> str:
    if mode == FAN_AUTO:
        return "A"
    if mode == FAN_QUIET:
        return "Q"
    return str(mode)


# --- Swing ------------------------------------------------------------------
def as_number(value) -> int | float | None:
    """A device value usable as a number, or None.

    Everything on the state topics is untrusted, and Home Assistant raises
    when a state cannot be rendered: strings, booleans, objects, NaN and
    infinity all have to be refused rather than passed on. Very large
    integers are refused too, since converting them can overflow.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        # Only to find out whether it is convertible at all; the value itself
        # stays an int, so a whole number is not displayed as "42.0".
        float(value)
    except OverflowError:
        return None
    return value


def as_bool(value) -> bool:
    """Truthiness of a device field, tolerating a stringified boolean.

    bool("false") is True, so a device or bridge that sends its booleans as
    strings would otherwise read as permanently on.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "off", "no")
    return bool(value)


SWING_OFF = "off"
SWING_ON = "on"
# Home Assistant models the two axes separately: SWING_MODE is the vertical
# one, SWING_HORIZONTAL_MODE the horizontal one, each simply on or off. The
# device has a boolean per axis, so the two line up directly.
SWING_MODES = [SWING_OFF, SWING_ON]


def swing_axis_to_ha(value) -> str:
    return SWING_ON if as_bool(value) else SWING_OFF


# --- Demand -----------------------------------------------------------------
# Output limit in percent. The device refuses anything below 30 (verified live
# against the firmware's own control, which offers 30..100), so a plain 0-100
# range would silently accept values that never take effect.
DEMAND_MIN = 30
DEMAND_MAX = 100
DEMAND_STEP = 5


def build_demand_command(value) -> dict:
    # round, not int(): Home Assistant validates a number service call against
    # min/max but not against the step, so an automation may pass any float in
    # range. Truncating 54.999... would send 54 - a whole step below what was
    # asked for, silently.
    return {"demand": round(value)}


# --- Temperature / entity sets ----------------------------------------------
TEMP_MIN = 16.0
TEMP_MAX = 32.0  # firmware HA discovery reports max_temp 32
TEMP_STEP = 0.5
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
    if not as_bool(data.get("power", False)):
        return HVAC_OFF
    mode = data.get("mode")
    return MODE_DEV_TO_HA.get(mode) if isinstance(mode, str) else None


def hvac_action_from_state(data: dict) -> str | None:
    """What the unit is doing right now, or ``None`` when that is not knowable.

    The device has no dedicated action field (checked live against the running
    firmware), so this is derived. ``comp`` is the compressor frequency: at zero
    the unit is circulating air but neither heating nor cooling, which is
    exactly Home Assistant's "idle". Without that check a unit sitting at its
    setpoint would keep claiming to cool.

    Only S21 reports it - the CN_WIRED and X50A decoders never set it - so on
    those the selected mode is all there is to go on. That names the direction
    correctly and can overstate a unit resting at its setpoint, which is the
    better half of the trade against reporting nothing at all.
    """
    if not as_bool(data.get("power", False)):
        return ACTION_OFF
    if as_bool(data.get("antifreeze")):
        # The firmware makes the same call: anti-freeze suspends cooling and
        # is reported as defrosting rather than as whatever the mode says.
        return ACTION_DEFROSTING
    mode = data.get("mode")
    if mode == "F":
        return ACTION_FAN
    # as_number rejects what a frequency cannot be: a bool, and NaN or an
    # infinity, both of which json.loads accepts and neither of which can be
    # compared into a sensible answer. The sensors drop them for the same
    # reason, and this path must not be the one place that lets them through.
    comp = as_number(data.get("comp"))
    if comp is not None and comp < 0:
        comp = None  # not a frequency a compressor can run at
    running = None if comp is None else comp > 0
    if running is False:
        return ACTION_IDLE
    if as_bool(data.get("heat")):
        return ACTION_HEATING
    if mode == "C":
        return ACTION_COOLING
    if mode == "D":
        return ACTION_DRYING
    # In heat_cool the direction is not knowable. `heat` cannot answer it: on
    # S21 the firmware derives it as `mode == HEAT` and says so itself
    # ("Crude - TODO find if anything actually tells us this"), so it is false
    # in heat_cool even while the unit heats. Reporting idle here would claim
    # the unit is doing nothing while the compressor runs, so say nothing:
    # Home Assistant renders an absent action as unknown, which is the truth.
    return None


# --- Command builders -------------------------------------------------------
def build_hvac_mode_command(ha_mode: str) -> dict:
    if ha_mode == HVAC_OFF:
        return {"power": False}
    return {"power": True, "mode": MODE_HA_TO_DEV[ha_mode]}


def build_temperature_command(temp) -> dict:
    return {"temp": temp}


def build_fan_command(ha_fan: str) -> dict:
    return {"fan": fan_ha_to_dev(ha_fan)}


def build_swing_command(field: str, mode: str) -> dict:
    """Set one swing axis; `field` is "swingv" or "swingh"."""
    return {field: mode == SWING_ON}


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
