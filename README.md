# Faikout — Home Assistant Integration

Home Assistant integration for Daikin air conditioners fitted with a
[Faikin/Faikout](https://github.com/revk/ESP32-Faikin) module (RevK firmware), over MQTT.

The module publishes its state and accepts commands on MQTT. This integration subscribes to those topics
and exposes the unit as a proper climate device, with the sensors and toggles the firmware reports.

```
Faikin/Faikout module  ──MQTT──>  broker  ──>  Home Assistant  ──>  this integration
```

## First: you may not need this at all

The firmware has **built-in Home Assistant support** and for most setups that is the better choice —
nothing to install, and it is maintained alongside the firmware itself. Try that first.

### The built-in route

1. In the module's web interface, enable its Home Assistant / MQTT auto-discovery setting, and point the
   module at your broker.
2. In Home Assistant, have the **MQTT integration** configured against that same broker, with discovery
   enabled (it is on by default).
3. The unit appears by itself under *Settings → Devices & Services → MQTT*.

The module then publishes retained discovery configs under the `homeassistant/` topic prefix. To check
whether it is actually doing so:

```bash
mosquitto_sub -h your-broker -t 'homeassistant/#' -v
```

If entries scroll past, discovery is working and you are done — you do not need this integration.

### What each route gives you

Measured against a Faikin S21 unit; your model may report fewer fields.

| | Built-in discovery | This integration |
|---|---|---|
| Climate entity | yes | yes |
| Temperatures | outside, coil, plus the AC's own values | + inlet |
| Energy | 3 counters | 3 counters, converted to kWh |
| Switches | 9 | 9 |
| Faikout-Auto toggle | **yes** | no |
| Restart button, demand select | **yes** | no |
| Diagnostics (uptime, memory, WiFi, IP, build, …) | no | **14 entities** |
| Broker separate from Home Assistant's | no | **yes** |
| Update-rate throttle | no | **yes** |

So the built-in route is *not* a subset — it exposes a Faikout-Auto switch, a restart button and a demand
select that this integration does not have. Pick whichever matches what you need.

### Reasons to use this integration instead

- **A separate broker.** Home Assistant's MQTT integration connects to exactly one broker. If your Faikout
  publishes to a different one, discovery cannot reach it; this integration can open its own connection.
- **Diagnostics** as typed entities with proper device classes and units.
- **Control over the update rate**, which matters if the recorder database is growing faster than you like.

> **Do not run both.** Two sets of entities for one unit, with two entity IDs for every value, is a mess to
> undo later. Turn the firmware's discovery off before adding this integration — and if you already had it
> on, delete the MQTT device afterwards, since retained discovery configs otherwise bring it back.

## Requirements

- Home Assistant **2025.3** or newer. This is the first release shipping paho-mqtt 2.x, which the
  own-broker mode needs. It is enforced by a CI job pinned to exactly that version.
- Either Home Assistant's **MQTT integration** pointed at the broker your Faikout publishes to, **or** the
  broker's connection details so this integration can connect on its own.

## Installation

### HACS
1. HACS → ⋮ → *Custom repositories* → add this repository as an **Integration**.
2. Install **Faikout** and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration →* **Faikout**.

### Manual
Copy `custom_components/faikout` into your Home Assistant `config/custom_components/` directory and
restart.

## Setup

The config flow first asks **how** Home Assistant should reach the module:

| Choice | Use when |
|---|---|
| **Home Assistant's MQTT integration** | your Faikout publishes to the broker HA is already connected to |
| **An own MQTT broker** | the Faikout lives on a different broker than HA's MQTT client |

Either way the integration then listens briefly on `state/+` and offers the modules it found. You can also
type the hostname by hand — it is the middle part of the topics, the `GuestAC` in `state/GuestAC`.

> **The own-broker mode is LAN only.** It speaks plain MQTT — there is no TLS option, so the broker
> credentials and all control traffic travel unencrypted. Never use it across the internet. For a remote
> broker, point Home Assistant's own MQTT integration at it (which does support TLS) and pick the first
> option instead.

### Options

Reachable later via *Configure* on the integration entry:

- **Update interval** — how often incoming changes are pushed to the entities. Defaults to 10 seconds.
  The module reports on every change, which for a running unit is more often than most people need and
  writes a recorder row each time. The newest value is never lost, only delayed to the end of the window.
  Set `0` to pass every message straight through. Availability changes always bypass this.
- **Own MQTT client** — switch an existing entry between the two transports, with the broker details.

## Entities

| Platform | What |
|---|---|
| **Climate** | power, mode (heat/cool/auto/dry/fan only), target temperature, fan (auto, 1–5), swing |
| **Sensors** | room / outside / inlet / coil temperature, humidity, power, energy (total, heating, cooling), compressor frequency, fan speed, demand |
| **Diagnostics** | uptime, MQTT uptime, free memory, free SPI RAM, flash size, WiFi SSID/BSSID/channel/signal, IP address, reset reason, firmware build, protocol, last report |
| **Switches** | powerful, economy, streamer, quiet (outdoor), comfort, sensor mode, LED, vertical/horizontal swing |

Entities are only created for the fields your module actually reports, and appear automatically when a
field turns up for the first time. Some diagnostics are disabled by default — enable them in the entity
settings if you want them.

## Known limitations

- **The LED switch is disabled by default.** On S21 units a LED-only command does not trigger a frame to
  the indoor unit, so the new value only takes effect alongside the next real change. That makes it
  unreliable as a standalone switch.
- **No TLS** in own-broker mode, see above.
- **Faikout-Auto is not exposed** (target range, external reference, schedules). Use the module's own web
  interface for that.
- Two modules with the same hostname on different brokers are told apart by their MAC address, which is
  read during discovery. A hostname typed by hand has no MAC to read, so in that case the hostname alone
  identifies the device.

## Development

The device↔Home Assistant logic lives in `const.py` and imports no Home Assistant code, so it is unit
tested standalone. On top of that, the config flow, coordinator and entities are tested against a real
Home Assistant core with a fake MQTT transport — no broker required.

```bash
python -m venv .venv
pip install -r requirements-test.txt
pip install pytest-homeassistant-custom-component paho-mqtt   # for the full suite
pytest -q
```

The Home Assistant tests are Linux/macOS only — HA's test machinery imports `fcntl`. They skip themselves
elsewhere, so the pure suite still runs on Windows; use WSL2 there to run everything.

CI runs the suite against both the current and the minimum supported Home Assistant, plus `hassfest`.

## Credits

The [ESP32-Faikin](https://github.com/revk/ESP32-Faikin) firmware and hardware are by RevK. This
integration is an independent Home Assistant client for it and is not affiliated with that project or
with Daikin.

## License

MIT — see [LICENSE](LICENSE).
