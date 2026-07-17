# Faikout — Home Assistant Integration

Custom integration for Faikin/Faikout AC modules (RevK firmware) over MQTT.

**Data flow:** Faikout module → MQTT broker → Home Assistant MQTT integration → this integration.

## Requirements
- Home Assistant 2024.12 or newer
- The **MQTT integration** configured against the same broker the Faikout publishes to

## Installation (HACS)
1. HACS → Custom repositories → add this repo as an *Integration*.
2. Install "Faikout", restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Faikout**.
4. Pick the discovered module (or type its hostname) and submit.

## Entities
- **Climate** — power, mode (heat/cool/auto/dry/fan_only), target temp, fan (auto/quiet/1–5), swing.
- **Sensors** — room / outside / inlet / liquid temperature.
- **Switches** — powerful, econo, streamer, vertical/horizontal swing (only those your model reports).

## Not included
Faikout-Auto (target range / external `env` reference / schedules). Use the module's web UI for that.

## Development
Home Assistant is not installed locally (it does not build on Windows/py3.13). Pure logic lives HA-free in `const.py` and is unit-tested; the HA adapter modules are syntax-checked and verified on a real device.
```bash
python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements-test.txt
./.venv/Scripts/python.exe -m pytest -q
```
