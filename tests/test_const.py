import importlib.util
import pathlib

import pytest

# Load const.py directly from its file path instead of
# `from custom_components.faikout import const`. The latter would first
# execute `custom_components/faikout/__init__.py`, which imports
# `homeassistant` (not installed in this HA-free test environment). const.py
# itself has no such dependency, so it can be exercised standalone.
_CONST_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "faikout"
    / "const.py"
)
_spec = importlib.util.spec_from_file_location("faikout_const", _CONST_PATH)
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)


def test_topic_helpers():
    assert const.state_topic("GuestAC") == "state/GuestAC"
    assert const.status_topic("GuestAC") == "state/GuestAC/status"
    assert const.control_topic("GuestAC") == "command/GuestAC/control"
    assert const.DISCOVERY_TOPIC == "state/+"


def test_mode_mapping_roundtrip():
    assert const.MODE_DEV_TO_HA == {
        "H": "heat", "C": "cool", "A": "auto", "D": "dry", "F": "fan_only",
    }
    assert const.MODE_HA_TO_DEV["heat"] == "H"
    assert const.MODE_HA_TO_DEV["fan_only"] == "F"
    assert const.HVAC_MODES[0] == "off"


# Fan value "Q" is the quiet/night step. The separate "quiet" boolean flag is
# the outdoor quiet setting, unrelated to this.
@pytest.mark.parametrize(
    "dev,ha", [("A", "auto"), ("Q", "quiet"), ("q", "quiet"), ("a", "auto"), (1, "1"), ("3", "3")]
)
def test_fan_dev_to_ha(dev, ha):
    assert const.fan_dev_to_ha(dev) == ha


def test_fan_dev_to_ha_none():
    assert const.fan_dev_to_ha(None) is None


def test_fan_modes_include_quiet_step():
    assert const.FAN_MODES == ["auto", "quiet", "1", "2", "3", "4", "5"]


def test_fan_quiet_roundtrip():
    assert const.fan_ha_to_dev("quiet") == "Q"
    assert const.fan_dev_to_ha("Q") == "quiet"


def test_quiet_is_also_a_switch_field():
    # The outdoor-quiet boolean is a different device function from the fan step.
    assert "quiet" in const.SWITCH_FIELDS


def test_device_metadata_from_bare_status():
    meta = {
        "app": "Faikout",
        "version": "3087afa9",
        "build-suffix": "-S3-MINI-N4-R2",
        "id": "A1B2C3D4E5F6",
    }
    assert const.device_metadata(meta) == {
        "model": "Faikout S3-MINI-N4-R2",
        "sw_version": "3087afa9",
        # Normalised on the way out, because this reaches the device registry.
        "mac": "a1:b2:c3:d4:e5:f6",
    }


def test_device_metadata_defaults():
    assert const.device_metadata({}) == {
        "model": "Faikout",
        "sw_version": None,
        "mac": None,
    }


# The device only acts on the string form of a fan level, not the number.
@pytest.mark.parametrize("ha,dev", [("auto", "A"), ("1", "1"), ("5", "5")])
def test_fan_ha_to_dev(ha, dev):
    result = const.fan_ha_to_dev(ha)
    assert result == dev
    assert isinstance(result, str)


@pytest.mark.parametrize("v,h,expected", [
    (False, False, "off"), (True, False, "vertical"),
    (False, True, "horizontal"), (True, True, "both"), (None, None, "off"),
])
def test_swing_dev_to_ha(v, h, expected):
    assert const.swing_dev_to_ha(v, h) == expected


@pytest.mark.parametrize("mode,expected", [
    ("off", {"swingv": False, "swingh": False}),
    ("vertical", {"swingv": True, "swingh": False}),
    ("horizontal", {"swingv": False, "swingh": True}),
    ("both", {"swingv": True, "swingh": True}),
])
def test_swing_ha_to_dev(mode, expected):
    assert const.swing_ha_to_dev(mode) == expected


# --- state readers ---
def test_hvac_mode_from_state_off_when_power_false():
    assert const.hvac_mode_from_state({"power": False, "mode": "C"}) == "off"


def test_hvac_mode_from_state_uses_mode_when_on():
    assert const.hvac_mode_from_state({"power": True, "mode": "H"}) == "heat"


def test_hvac_mode_from_state_unknown_mode_is_none():
    assert const.hvac_mode_from_state({"power": True, "mode": "?"}) is None


@pytest.mark.parametrize("data,expected", [
    ({"power": False}, "off"),
    ({"power": True, "heat": True}, "heating"),
    ({"power": True, "mode": "C"}, "cooling"),
    ({"power": True, "mode": "D"}, "drying"),
    ({"power": True, "mode": "F"}, "fan"),
    ({"power": True, "mode": "A"}, "idle"),
])
def test_hvac_action_from_state(data, expected):
    assert const.hvac_action_from_state(data) == expected


# --- command builders ---
def test_build_hvac_mode_command_off():
    assert const.build_hvac_mode_command("off") == {"power": False}


def test_build_hvac_mode_command_cool():
    assert const.build_hvac_mode_command("cool") == {"power": True, "mode": "C"}


def test_build_temperature_command():
    assert const.build_temperature_command(23.5) == {"temp": 23.5}


def test_build_fan_command_numeric():
    assert const.build_fan_command("3") == {"fan": "3"}


def test_build_fan_command_auto():
    assert const.build_fan_command("auto") == {"fan": "A"}


def test_build_swing_command_both():
    assert const.build_swing_command("both") == {"swingv": True, "swingh": True}


def test_build_switch_command():
    assert const.build_switch_command("econo", True) == {"econo": True}
    assert const.build_switch_command("streamer", False) == {"streamer": False}


# --- merge_state ---
def test_merge_state_json():
    merged = const.merge_state({"power": False}, '{"power": true, "temp": 21}')
    assert merged == {"power": True, "temp": 21}


def test_merge_state_presence_false():
    assert const.merge_state({"power": True}, "false") == {"power": True, "online": False}


def test_merge_state_presence_true():
    assert const.merge_state(None, "true") == {"online": True}


def test_merge_state_invalid_json_returns_none():
    assert const.merge_state({"power": True}, "not json") is None


def test_merge_state_non_object_returns_none():
    assert const.merge_state({}, "[1,2,3]") is None


# --- host validation --------------------------------------------------------
@pytest.mark.parametrize(
    "host", ["GuestAC", "faikin-1", "ac_2", "hall.faikin"]
)
def test_is_valid_host_accepts_normal_names(host):
    assert const.is_valid_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        " lead",
        "trail ",
        "with space",
        "a/b",      # topic level separator
        "a+b",      # single-level wildcard
        "a#b",      # multi-level wildcard
        "#",
        "+",
        "bad\nline",
    ],
)
def test_is_valid_host_rejects_topic_breaking_names(host):
    assert not const.is_valid_host(host)


# --- device identity --------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AABBCCDDEEFF", "aa:bb:cc:dd:ee:ff"),
        ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
        ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
        ("", None),
        (None, None),
        ("not-a-mac", None),
        ("AABBCCDDEE", None),   # too short
    ],
)
def test_normalize_mac(raw, expected):
    assert const.normalize_mac(raw) == expected


def test_device_id_prefers_mac():
    assert const.device_id_for("AABBCCDDEEFF", "GuestAC") == "aa:bb:cc:dd:ee:ff"


def test_device_id_falls_back_to_host_without_mac():
    assert const.device_id_for(None, "GuestAC") == "GuestAC"
    assert const.device_id_for("garbage", "GuestAC") == "GuestAC"


def test_same_host_different_mac_gives_different_identity():
    a = const.device_id_for("111111111111", "GuestAC")
    b = const.device_id_for("222222222222", "GuestAC")
    assert a != b


# --- bounds on untrusted payloads -------------------------------------------
def test_merge_state_rejects_oversized_payload():
    huge = '{"a": "' + "x" * (const.MAX_PAYLOAD_CHARS + 10) + '"}'
    assert const.merge_state({"home": 20}, huge) is None


def test_merge_state_caps_new_field_count():
    """A device streaming fresh key names must not grow state without limit."""
    import json as _json

    state = {}
    for chunk in range(6):
        payload = _json.dumps(
            {f"k{chunk}_{i}": 1 for i in range(100)}
        )
        state = const.merge_state(state, payload)
    assert len(state) == const.MAX_STATE_FIELDS


def test_merge_state_still_updates_known_fields_at_the_cap():
    """The cap must not freeze real values once it is reached."""
    import json as _json

    state = {f"filler{i}": 0 for i in range(const.MAX_STATE_FIELDS)}
    state["home"] = 20
    merged = const.merge_state(state, _json.dumps({"home": 25, "brandnew": 1}))
    assert merged["home"] == 25          # known field updates
    assert "brandnew" not in merged      # new one refused at the cap


def test_device_metadata_normalises_mac():
    meta = const.device_metadata({"id": "AABBCCDDEEFF"})
    assert meta["mac"] == "aa:bb:cc:dd:ee:ff"


def test_device_metadata_drops_garbage_mac():
    """Only a real MAC may reach the device registry."""
    assert const.device_metadata({"id": "x" * 500})["mac"] is None
    assert const.device_metadata({"id": "not-a-mac"})["mac"] is None


def test_device_metadata_truncates_text_fields():
    meta = const.device_metadata({"app": "A" * 500, "version": "V" * 500})
    assert len(meta["model"]) <= const.MAX_META_TEXT
    assert len(meta["sw_version"]) <= const.MAX_META_TEXT


def test_default_update_interval_coalesces():
    """Default is a throttle, not real-time: 10s, and 0 stays available."""
    assert const.DEFAULT_UPDATE_INTERVAL == 10


# --- TLS port handling ------------------------------------------------------
def test_effective_port_moves_untouched_default_to_tls_port():
    assert const.effective_port(1883, True) == 8883


def test_effective_port_keeps_plain_default_without_tls():
    assert const.effective_port(1883, False) == 1883


def test_effective_port_honours_an_explicit_choice():
    """MQTTS on a non-standard port is legitimate and must not be overridden."""
    assert const.effective_port(9001, True) == 9001
    assert const.effective_port(8883, True) == 8883
    assert const.effective_port(9001, False) == 9001


def test_effective_port_cannot_express_tls_on_1883():
    """Documented limitation, pinned so it is a decision and not a surprise."""
    assert const.effective_port(1883, True) == 8883


# --- demand ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (85, 85),
        (85.0, 85),
        (54.999999999999, 55),   # float arithmetic in an automation
        (67.8, 68),
        (67.2, 67),
    ],
)
def test_build_demand_command_rounds(value, expected):
    """A service call may carry any float in range, not just slider steps."""
    assert const.build_demand_command(value) == {"demand": expected}


def test_demand_bounds_match_the_device():
    """The device offers 30..100 in steps of 5; anything below 30 is refused."""
    assert (const.DEMAND_MIN, const.DEMAND_MAX, const.DEMAND_STEP) == (30, 100, 5)


# --- protocol-dependent capabilities -----------------------------------------
@pytest.mark.parametrize(
    ("protocol", "expected"),
    [("S21", 0.5), ("CN_WIRED", 1.0), ("CN-WIRED", 1.0), ("X50", 0.1), (None, 0.5)],
)
def test_temp_step_follows_protocol(protocol, expected):
    assert const.temp_step_for(protocol) == expected


def test_cn_wired_has_only_three_manual_fan_steps():
    """The firmware leaves 2 and 4 unused on those units."""
    modes = const.fan_modes_for("CN_WIRED")
    assert modes == ["auto", "quiet", "1", "3", "5"]
    assert "2" not in modes and "4" not in modes


def test_other_protocols_keep_five_steps():
    assert const.fan_modes_for("S21") == const.FAN_MODES
    assert const.fan_modes_for(None) == const.FAN_MODES


# --- hvac_action -------------------------------------------------------------
def test_action_idle_when_compressor_is_off():
    """At the setpoint the unit blows air but is not cooling."""
    data = {"power": True, "mode": "C", "comp": 0}
    assert const.hvac_action_from_state(data) == "idle"


def test_action_cooling_when_compressor_runs():
    assert const.hvac_action_from_state({"power": True, "mode": "C", "comp": 42}) == "cooling"


def test_action_without_compressor_field_falls_back_to_mode():
    """Older payloads have no comp; behaviour must not regress to unknown."""
    assert const.hvac_action_from_state({"power": True, "mode": "C"}) == "cooling"


def test_action_fan_only_ignores_compressor():
    assert const.hvac_action_from_state({"power": True, "mode": "F", "comp": 0}) == "fan"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (1, True), (0, False), (None, False),
     ("false", False), ("False", False), ("0", False), ("off", False),
     ("true", True), ("1", True)],
)
def test_as_bool_handles_stringified_booleans(value, expected):
    """bool("false") is True, which would read as permanently on."""
    assert const.as_bool(value) is expected
