# Faikout — Home Assistant Integration

Custom integration for Faikin/Faikout AC modules (RevK firmware) over MQTT.

**Data flow:** Faikout module → MQTT broker → Home Assistant MQTT integration → this integration.

## Requirements
- Home Assistant 2024.12 or newer
- Either the **MQTT integration** configured against the broker the Faikout publishes to, **or** the broker
  details (host/port/credentials) so the integration can connect on its own.

## Installation (HACS)
1. HACS → Custom repositories → add this repo as an *Integration*.
2. Install "Faikout", restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Faikout**.
4. Choose how to connect:
   - **Home Assistant's MQTT integration** — uses the broker HA is already connected to, or
   - **An own MQTT broker** — enter host/port/credentials; the integration connects directly. Useful when the
     Faikout lives on a different broker than HA's MQTT client.
5. Pick the discovered module (or type its hostname) and submit.

Both the connection and an optional update-interval throttle can be changed later under the integration's
*Configure* options.

## Entities
- **Climate** — power, mode (heat/cool/auto/dry/fan_only), target temperature, fan (auto, 1–5), swing (vertical/horizontal).
- **Sensors** — room / outside / liquid temperature, humidity, power, energy (heating & cooling, kWh), fan speed, demand.
- **Switches** — powerful, econo, streamer, quiet, vertical/horizontal swing.

Sensors and switches are created for the fields your module actually reports, and appear automatically as new fields show up.

## Not included
Faikout-Auto (target range / external `env` reference / schedules). Use the module's web UI for that.

## Development
The pure device↔HA logic lives in `const.py` (no Home Assistant import) and is unit-tested. The HA adapter modules are covered by an import smoke test that runs whenever Home Assistant is installed.

```bash
python -m venv .venv
pip install -r requirements-test.txt   # add `homeassistant` to also run the import tests
pytest -q
```
