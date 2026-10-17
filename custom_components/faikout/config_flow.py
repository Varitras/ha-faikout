"""Config flow for Faikout: discover modules on the broker, pick a host."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import probatio
from homeassistant.components import mqtt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    CONF_MQTT_CLEAR_PASSWORD,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TLS,
    CONF_MQTT_TLS_INSECURE,
    CONF_MQTT_USERNAME,
    CONF_UPDATE_INTERVAL,
    CONF_USE_OWN_MQTT,
    DEFAULT_MQTT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DISCOVERY_TOPIC,
    DOMAIN,
    device_id_for,
    effective_port,
    error_kind,
    is_valid_host,
    normalize_mac,
)
from .transport import (
    MqttConnectionRefused,
    async_discover_on_broker,
    collect_module,
)

_LOGGER = logging.getLogger(__name__)

DISCOVERY_SECONDS = 3.0


def _broker_from_input(user_input: Mapping) -> dict:
    return {
        CONF_MQTT_HOST: user_input[CONF_MQTT_HOST].strip(),
        CONF_MQTT_PORT: int(user_input.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)),
        CONF_MQTT_USERNAME: user_input.get(CONF_MQTT_USERNAME, ""),
        CONF_MQTT_PASSWORD: user_input.get(CONF_MQTT_PASSWORD, ""),
        CONF_MQTT_TLS: bool(user_input.get(CONF_MQTT_TLS, False)),
        CONF_MQTT_TLS_INSECURE: bool(user_input.get(CONF_MQTT_TLS_INSECURE, False)),
    }


def _prefilled(value) -> dict:
    """Show a stored value in an optional field without making it its default.

    The frontend leaves an emptied optional field out of the submission, and a
    default would then put the stored value straight back: the field could
    never be cleared.
    """
    return {"suggested_value": value}


async def _probe_broker(hass, broker: dict) -> tuple[dict, str | None]:
    """Connect with these details; the modules found, or a form error key."""
    try:
        hosts = await async_discover_on_broker(
            hass,
            broker[CONF_MQTT_HOST],
            effective_port(broker[CONF_MQTT_PORT], broker[CONF_MQTT_TLS]),
            broker[CONF_MQTT_USERNAME] or None,
            broker[CONF_MQTT_PASSWORD] or None,
            DISCOVERY_SECONDS,
            broker[CONF_MQTT_TLS],
            broker[CONF_MQTT_TLS_INSECURE],
        )
    except MqttConnectionRefused as err:
        _LOGGER.debug("Broker refused the connection: %s", error_kind(err))
        return {}, "invalid_auth" if err.is_auth_failure else "cannot_connect"
    except Exception as err:  # noqa: BLE001 - any other connect problem
        _LOGGER.debug("Broker check failed: %s", error_kind(err))
        return {}, "cannot_connect"
    return hosts, None


def _broker_schema(defaults=None) -> probatio.Schema:
    """Broker connection form, shared by initial setup and re-authentication."""
    d = defaults or {}
    return probatio.Schema(
        {
            probatio.Required(
                CONF_MQTT_HOST, default=d.get(CONF_MQTT_HOST, probatio.UNDEFINED)
            ): selector.TextSelector(),
            probatio.Optional(
                CONF_MQTT_PORT,
                description=_prefilled(d.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            probatio.Optional(
                CONF_MQTT_USERNAME, description=_prefilled(d.get(CONF_MQTT_USERNAME, ""))
            ): selector.TextSelector(),
            probatio.Optional(CONF_MQTT_PASSWORD, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            probatio.Optional(
                CONF_MQTT_TLS, default=d.get(CONF_MQTT_TLS, False)
            ): selector.BooleanSelector(),
            probatio.Optional(
                CONF_MQTT_TLS_INSECURE,
                default=d.get(CONF_MQTT_TLS_INSECURE, False),
            ): selector.BooleanSelector(),
        }
    )


def _host_selector(discovered):
    """Dropdown of discovered hosts (free text still allowed), else a text box."""
    if discovered:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(options=sorted(discovered), custom_value=True)
        )
    return selector.TextSelector()


class FaikoutConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Faikout config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> FaikoutOptionsFlow:
        return FaikoutOptionsFlow()

    def __init__(self) -> None:
        self._broker: dict = {}
        # hostname -> MAC (None when the module did not announce one)
        self._discovered: dict = {}

    def _duplicate_of_existing(self, host: str, mac) -> bool:
        """Whether this module is already set up under a different identity.

        A module discovered with its MAC and the same module added by hand (no
        MAC, so identified by hostname) produce different unique ids, so the
        unique-id check alone would happily add it twice.

        Two entries may share a hostname only when both carry a MAC, the MACs
        differ, AND they sit on different brokers — that is the genuine "same
        name on two brokers" case. On one broker the hostname is the topic, so
        a second entry would subscribe to and control the very same topics as
        the first, which is what happens after a device is swapped out.
        """
        new_mac = normalize_mac(mac)
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) != host:
                continue
            existing_mac = normalize_mac(entry.data.get(CONF_MAC))
            if existing_mac is None or new_mac is None:
                return True
            if self._same_broker(entry):
                return True
        return False

    def _broker_id(self, options) -> tuple:
        """The broker endpoint an entry actually talks to.

        Home Assistant's own MQTT client is resolved to its configured host
        and port rather than treated as a category of its own: an entry using
        it and an entry pointing straight at the same machine are on the same
        broker, and would otherwise both be allowed to drive the same topics.
        """
        if not options.get(CONF_USE_OWN_MQTT):
            return self._ha_broker_id()
        tls = bool(options.get(CONF_MQTT_TLS))
        return (
            str(options.get(CONF_MQTT_HOST, "")).strip().lower(),
            # The port actually connected to, and TLS as well: a stored 1883
            # with TLS on really means 8883, and plaintext 1883 is a different
            # broker endpoint from TLS 1883.
            effective_port(options.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT), tls),
            tls,
        )

    def _ha_broker_id(self) -> tuple:
        """Where Home Assistant's own MQTT integration is pointed."""
        for entry in self.hass.config_entries.async_entries("mqtt"):
            host = str(entry.data.get("broker", "")).strip().lower()
            if host:
                # Home Assistant records TLS as the CA it validates against,
                # so the presence of "certificate" is what makes its
                # connection encrypted. Without reading it, HA-over-TLS and
                # separate-client-over-TLS to the same broker look like two
                # different endpoints and both get to drive the same topics.
                tls = bool(entry.data.get("certificate"))
                return (host, int(entry.data.get("port", DEFAULT_MQTT_PORT)), tls)
        # Not configured, or it does not say: fall back to a marker so two
        # entries using it still match each other.
        return ("ha",)

    def _same_broker(self, entry) -> bool:
        pending = {CONF_USE_OWN_MQTT: bool(self._broker), **self._broker}
        return self._broker_id(entry.options) == self._broker_id(pending)

    def _rename(self, host: str, mac) -> tuple[ConfigEntry | None, str | None]:
        """The entry to follow to a new name, or why following it is refused.

        Same MAC, new name: the module was renamed and its topics moved with it.
        Only on the entry's broker, though - from another broker the entry would
        keep its broker and could land on a topic another entry there controls -
        and only once the old name is no longer announced with this MAC, since a
        retained status can outlive the rename. A refusal gets its own reason:
        "already configured" would tell the user the entry works when it does not.
        """
        for entry in self._async_current_entries(include_ignore=False):
            if entry.unique_id != self.unique_id:
                continue
            old = entry.data.get(CONF_HOST)
            if old == host:
                return None, None
            if not self._same_broker(entry):
                return None, "rename_other_broker"
            if normalize_mac(self._discovered.get(old)) == normalize_mac(mac):
                return None, "rename_old_name_still_announced"
            return entry, None
        return None, None

    def _follow_rename(self, entry: ConfigEntry, host: str) -> ConfigFlowResult:
        # A title still equal to the old hostname was never chosen by the user,
        # so it follows the module; one the user set stays.
        title_was_the_host = entry.title == entry.data.get(CONF_HOST)
        title = host if title_was_the_host else entry.title
        return self.async_update_reload_and_abort(
            entry, title=title, data_updates={CONF_HOST: host}, reason="module_renamed"
        )

    @staticmethod
    def _entry_data(host: str, mac) -> dict:
        """Freeze the identity at setup time so it never shifts later."""
        return {
            CONF_HOST: host,
            CONF_MAC: mac,
            CONF_DEVICE_ID: device_id_for(mac, host),
        }

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Let the user pick how the integration should reach the module."""
        return self.async_show_menu(
            step_id="user", menu_options=["ha_mqtt", "own_mqtt"]
        )

    # -- via Home Assistant's MQTT integration -------------------------------
    async def async_step_ha_mqtt(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not is_valid_host(host):
                errors["base"] = "invalid_host"
            else:
                mac = self._discovered.get(host)
                if self._duplicate_of_existing(host, mac):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(device_id_for(mac, host))
                renamed, refused = self._rename(host, mac)
                if refused is not None:
                    return self.async_abort(reason=refused)
                if renamed is not None:
                    return self._follow_rename(renamed, host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=host, data=self._entry_data(host, mac)
                )

        self._discovered = await self._discover_hosts()
        return self.async_show_form(
            step_id="ha_mqtt",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_HOST): _host_selector(self._discovered)}
            ),
            errors=errors,
        )

    # -- via your own broker -------------------------------------------------
    async def async_step_own_mqtt(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Collect broker details and discover modules on that broker."""
        errors: dict[str, str] = {}
        if user_input is not None:
            broker = _broker_from_input(user_input)
            self._discovered, error = await _probe_broker(self.hass, broker)
            if error is None:
                self._broker = broker
                return await self.async_step_own_host()
            errors["base"] = error

        return self.async_show_form(
            step_id="own_mqtt", data_schema=_broker_schema(), errors=errors
        )

    # -- re-authentication ---------------------------------------------------
    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        """Broker rejected our credentials; ask the user for new ones."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            broker = _broker_from_input(user_input)
            _, error = await _probe_broker(self.hass, broker)
            if error is None:
                # Reloaded here because nothing else would: there is no update
                # listener, and OptionsFlowWithReload covers only the options.
                return self.async_update_reload_and_abort(
                    entry, options={**entry.options, **broker}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_broker_schema(entry.options),
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )

    async def async_step_own_host(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Pick the module found on your own broker."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not is_valid_host(host):
                errors["base"] = "invalid_host"
            else:
                mac = self._discovered.get(host)
                if self._duplicate_of_existing(host, mac):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(device_id_for(mac, host))
                renamed, refused = self._rename(host, mac)
                if refused is not None:
                    return self.async_abort(reason=refused)
                if renamed is not None:
                    return self._follow_rename(renamed, host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=host,
                    data=self._entry_data(host, mac),
                    options={CONF_USE_OWN_MQTT: True, **self._broker},
                )

        return self.async_show_form(
            step_id="own_host",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_HOST): _host_selector(self._discovered)}
            ),
            errors=errors,
        )

    async def _discover_hosts(self) -> dict:
        """Listen briefly on state/+ and collect modules with their MACs."""
        hosts: dict = {}

        @callback
        def _on_message(msg: mqtt.ReceiveMessage) -> None:
            collect_module(hosts, msg.topic, msg.payload)

        unsub = await mqtt.async_subscribe(self.hass, DISCOVERY_TOPIC, _on_message)
        try:
            await asyncio.sleep(DISCOVERY_SECONDS)
        finally:
            unsub()
        return hosts


class FaikoutOptionsFlow(OptionsFlowWithReload):
    """Options: update throttle and an optional separate MQTT client."""

    def _password_to_store(self, submitted: dict) -> str:
        """Credentials are a pair, and the form never shows the stored password.

        No username means no password, whatever was typed. With a username, a
        blank field means unchanged, and removing the password is its own
        action - there is nothing on the form to delete otherwise.
        """
        if not submitted.get(CONF_MQTT_USERNAME):
            return ""
        if submitted.get(CONF_MQTT_CLEAR_PASSWORD):
            return ""
        typed = submitted.get(CONF_MQTT_PASSWORD, "")
        if typed:
            return typed
        return self.config_entry.options.get(CONF_MQTT_PASSWORD, "")

    async def _check_changed_broker(self, submitted: dict) -> str | None:
        """Probe a broker the form changed, before it is saved.

        Saved unchecked, a mistyped host only surfaces after the reload, as an
        endless setup retry instead of an error on this form.
        """
        if not submitted.get(CONF_USE_OWN_MQTT):
            return None
        broker = _broker_from_input(submitted)
        stored = self.config_entry.options
        unchanged = (
            stored.get(CONF_USE_OWN_MQTT)
            and stored.get(CONF_MQTT_HOST)
            and _broker_from_input(stored) == broker
        )
        if unchanged:
            return None
        _, error = await _probe_broker(self.hass, broker)
        return error

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_USE_OWN_MQTT) and not str(
                user_input.get(CONF_MQTT_HOST, "")
            ).strip():
                errors[CONF_MQTT_HOST] = "host_required"
            else:
                # Normalise like the setup and reauth paths do; a pasted host
                # with stray whitespace would otherwise only surface later as an
                # opaque connect failure.
                cleaned = dict(user_input)
                if cleaned.get(CONF_MQTT_HOST):
                    cleaned[CONF_MQTT_HOST] = cleaned[CONF_MQTT_HOST].strip()
                if CONF_MQTT_PORT in cleaned:
                    cleaned[CONF_MQTT_PORT] = int(cleaned[CONF_MQTT_PORT])
                cleaned[CONF_MQTT_PASSWORD] = self._password_to_store(cleaned)
                cleaned.pop(CONF_MQTT_CLEAR_PASSWORD, None)  # an action, not a setting
                error = await self._check_changed_broker(cleaned)
                if error is None:
                    return self.async_create_entry(data=cleaned)
                errors["base"] = error

        o = self.config_entry.options
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_UPDATE_INTERVAL,
                    default=o.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                probatio.Required(
                    CONF_USE_OWN_MQTT,
                    default=o.get(CONF_USE_OWN_MQTT, False),
                ): selector.BooleanSelector(),
                probatio.Optional(
                    CONF_MQTT_HOST, description=_prefilled(o.get(CONF_MQTT_HOST, ""))
                ): selector.TextSelector(),
                probatio.Optional(
                    CONF_MQTT_PORT,
                    description=_prefilled(o.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                probatio.Optional(
                    CONF_MQTT_USERNAME, description=_prefilled(o.get(CONF_MQTT_USERNAME, ""))
                ): selector.TextSelector(),
                # An empty default, never the stored value: defaults and
                # suggested values are serialised into the form response and
                # would hand the password to the browser. Blank means
                # "unchanged"; see _password_to_store.
                probatio.Optional(
                    CONF_MQTT_PASSWORD, default=""
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
                probatio.Optional(
                    CONF_MQTT_CLEAR_PASSWORD, default=False
                ): selector.BooleanSelector(),
                probatio.Optional(
                    CONF_MQTT_TLS, default=o.get(CONF_MQTT_TLS, False)
                ): selector.BooleanSelector(),
                probatio.Optional(
                    CONF_MQTT_TLS_INSECURE,
                    default=o.get(CONF_MQTT_TLS_INSECURE, False),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
