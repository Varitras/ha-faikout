"""Import smoke test for the HA-dependent modules.

These modules import ``homeassistant`` and cannot be exercised by the pure
``test_const`` suite. Importing each one catches HA-API breakage that a plain
syntax check (``py_compile``) cannot — e.g. a symbol that moved to a different
module between HA releases.

Skipped automatically where Home Assistant is not installed (so the pure
``test_const`` suite still runs standalone). Runs on CI and on any environment
with ``homeassistant`` available.
"""

import importlib

import pytest

pytest.importorskip("homeassistant")

MODULES = [
    "custom_components.faikout",
    "custom_components.faikout.const",
    "custom_components.faikout.coordinator",
    "custom_components.faikout.entity",
    "custom_components.faikout.config_flow",
    "custom_components.faikout.climate",
    "custom_components.faikout.sensor",
    "custom_components.faikout.switch",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)
