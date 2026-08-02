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
        # "A" is heat_cool, not auto: the device picks the direction while the
        # user still sets the target. Home Assistant's auto means a schedule
        # sets it and the user cannot.
        "H": "heat", "C": "cool", "A": "heat_cool", "D": "dry", "F": "fan_only",
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


# Home Assistant drives the two axes separately, so each command names one.
@pytest.mark.parametrize(
    ("field", "mode", "expected"),
    [
        ("swingv", "on", {"swingv": True}),
        ("swingv", "off", {"swingv": False}),
        ("swingh", "on", {"swingh": True}),
        ("swingh", "off", {"swingh": False}),
    ],
)
def test_build_swing_command_sets_one_axis(field, mode, expected):
    assert const.build_swing_command(field, mode) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, "on"), (False, "off"), ("true", "on"), ("false", "off"), (None, "off")],
)
def test_swing_axis_to_ha(value, expected):
    assert const.swing_axis_to_ha(value) == expected


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
    [
        # The firmware's own spellings: prototype[] = S21, X50A, CN_WIRED,
        # Altherma_S. "X50" is not one of them.
        ("S21", 0.5),
        ("CN_WIRED", 1.0),
        ("CN-WIRED", 1.0),
        ("X50A", 0.1),
        ("Altherma_S", 0.1),
        (None, 0.5),
        ("something else", 0.5),
    ],
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


# --- hvac_action: idle applies to every mode, not just cooling --------------
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # compressor running -> actually working
        ({"power": True, "mode": "H", "heat": True, "comp": 40}, "heating"),
        ({"power": True, "mode": "C", "comp": 40}, "cooling"),
        ({"power": True, "mode": "D", "comp": 40}, "drying"),
        # compressor stopped -> idle, whatever the mode claims
        ({"power": True, "mode": "H", "heat": True, "comp": 0}, "idle"),
        ({"power": True, "mode": "C", "comp": 0}, "idle"),
        ({"power": True, "mode": "D", "comp": 0}, "idle"),
        # fan-only never involves the compressor
        ({"power": True, "mode": "F", "comp": 0}, "fan"),
        # no compressor reading at all -> fall back to the mode
        ({"power": True, "mode": "C"}, "cooling"),
        ({"power": True, "mode": "H", "heat": True}, "heating"),
        # off wins over everything
        ({"power": False, "mode": "H", "heat": True, "comp": 40}, "off"),
    ],
)
def test_hvac_action_idle_when_compressor_is_stopped(data, expected):
    assert const.hvac_action_from_state(data) == expected


def test_hvac_action_ignores_a_bool_compressor_value():
    """True would compare <= 0 as 1; a bool must not be read as a frequency."""
    assert const.hvac_action_from_state({"power": True, "mode": "C", "comp": False}) == "cooling"


# --- auto mode has no cooling flag of its own -------------------------------
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # auto, compressor turning, device says not heating -> it is cooling
        ({"power": True, "mode": "A", "comp": 35, "heat": False}, "cooling"),
        # auto and heating says so itself
        ({"power": True, "mode": "A", "heat": True, "comp": 35}, "heating"),
        # auto, compressor stopped -> idle
        ({"power": True, "mode": "A", "comp": 0}, "idle"),
        # auto without a compressor reading: nothing can be inferred
        ({"power": True, "mode": "A", "heat": False}, "idle"),
    ],
)
def test_hvac_action_in_auto_mode(data, expected):
    assert const.hvac_action_from_state(data) == expected


@pytest.mark.parametrize(
    "reported", ["CN_WIRED", "cn-wired", "CN WIRED", " cn_wired "]
)
def test_protocol_name_separators_are_tolerated(reported):
    assert const.fan_modes_for(reported) == const.FAN_MODES_3
    assert const.temp_step_for(reported) == 1.0


# --- protocol names as the firmware really publishes them -------------------
@pytest.mark.parametrize(
    ("reported", "step"),
    [
        # An inverted line appends a marker to the name.
        ("S21\u00acTx", 0.5),
        ("S21\u00acTx\u00acRx", 0.5),
        ("CN_WIRED\u00acTx", 1.0),
        ("X50A\u00acRx", 0.1),
    ],
)
def test_inversion_suffix_does_not_hide_the_protocol(reported, step):
    assert const.temp_step_for(reported) == step


def test_inverted_cn_wired_still_gets_three_fan_steps():
    assert const.fan_modes_for("CN_WIRED\u00acTx") == const.FAN_MODES_3


def test_loopback_falls_back_instead_of_guessing():
    """The firmware reports "loopback" instead of a protocol while looped."""
    assert const.temp_step_for("loopback") == const.TEMP_STEP


# --- anti-freeze ------------------------------------------------------------
def test_antifreeze_reports_defrosting():
    data = {"power": True, "mode": "H", "heat": True, "comp": 40, "antifreeze": True}
    assert const.hvac_action_from_state(data) == "defrosting"


def test_antifreeze_off_does_not_hide_the_real_action():
    data = {"power": True, "mode": "C", "comp": 40, "antifreeze": False}
    assert const.hvac_action_from_state(data) == "cooling"


def test_antifreeze_absent_is_not_defrosting():
    assert const.hvac_action_from_state({"power": True, "mode": "C", "comp": 40}) == "cooling"


# --- stringified booleans everywhere ----------------------------------------
@pytest.mark.parametrize("off", [False, "false", "False", "0", "off", ""])
def test_power_off_as_a_string_is_still_off(off):
    assert const.hvac_action_from_state({"power": off, "mode": "C"}) == "off"
    assert const.hvac_mode_from_state({"power": off, "mode": "C"}) == "off"


def test_heat_as_a_string_still_means_heating():
    data = {"power": "true", "mode": "H", "heat": "true", "comp": 40}
    assert const.hvac_action_from_state(data) == "heating"


# --- what counts as a usable number -----------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (42, 42),          # an int stays an int, not "42.0"
        (21.5, 21.5),
        (0, 0),
        (-5, -5),
        ("42", None),      # a string is not a number
        (True, None),      # a bool is not a measurement
        (None, None),
        ({"a": 1}, None),
        ([1], None),
        (float("nan"), None),
        (float("inf"), None),
        (10**400, None),   # too large to convert
    ],
)
def test_as_number(raw, expected):
    assert const.as_number(raw) == expected
    if expected is not None:
        assert type(const.as_number(raw)) is type(expected)


def test_auto_cooling_is_only_inferred_when_the_device_says_it_is_not_heating():
    """Without the heat flag the direction is unknown; do not assert one."""
    with_flag = {"power": True, "mode": "A", "comp": 35, "heat": False}
    without = {"power": True, "mode": "A", "comp": 35}
    assert const.hvac_action_from_state(with_flag) == "cooling"
    assert const.hvac_action_from_state(without) == "idle"


def test_device_auto_is_heat_cool_not_auto():
    """Home Assistant's auto means the user cannot set a temperature."""
    assert const.MODE_HA_TO_DEV["heat_cool"] == "A"
    assert "auto" not in const.HVAC_MODES
    assert const.hvac_mode_from_state({"power": True, "mode": "A"}) == "heat_cool"


def test_setting_heat_cool_sends_the_device_auto_mode():
    assert const.build_hvac_mode_command("heat_cool") == {"power": True, "mode": "A"}
