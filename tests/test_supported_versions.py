"""The version claims outside the code have to agree with what CI proves.

Dependabot keeps the actions current, but it never reads a claim: the minimum
Home Assistant version lives in hacs.json, in the README and in the CI lane
that actually installs it, and nothing stops those three from drifting apart.
A claimed minimum no lane installs is a promise nobody checks.
"""
import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).parents[1]
# Every workflow, not one file: a guard pinned to a single path goes blind
# the moment a second workflow arrives, and stays green while it does.
WORKFLOW_FILES = sorted((REPO / ".github/workflows").glob("*.yml"))
WORKFLOW = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOW_FILES)

# Actions deliberately tracked on a moving ref: both are published by the
# projects they validate against and carry no versioned releases to pin.
MOVING_REFS = ("home-assistant/actions/hassfest@master", "hacs/action@main")


def _claimed_minimum() -> str:
    return json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]


def test_minimum_version_is_the_one_a_ci_lane_installs():
    """hacs.json may only claim a minimum that a test lane really runs."""
    minimum = _claimed_minimum()
    series = minimum.rsplit(".", 1)[0]  # 2025.3.0 -> the 2025.3 series
    labels = re.findall(r"- label: (.+)", WORKFLOW)
    assert any(
        label.startswith("minimum HA") and series in label for label in labels
    ), f"no CI lane installs the claimed minimum {minimum}: lanes are {labels}"


def test_readme_states_the_same_minimum():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    series = _claimed_minimum().rsplit(".", 1)[0]
    assert f"**{series}** or newer" in readme


def test_workflow_directory_is_not_empty():
    """The scan above is only worth as much as the files it found."""
    assert WORKFLOW_FILES, "no workflow files found - the guards below scan nothing"


def test_every_action_is_pinned_or_a_known_moving_ref():
    """A new action added on a branch would silently change under us."""
    unpinned = [
        used
        for used in re.findall(r"uses: (\S+)", WORKFLOW)
        if used not in MOVING_REFS and not re.search(r"@(v\d|[0-9a-f]{40})", used)
    ]
    assert not unpinned, f"not pinned to a version or an allowed moving ref: {unpinned}"


def test_the_claimed_minimum_provides_what_the_code_relies_on():
    """The floor is not a number we picked - it is the release that has the API.

    2026.10 is the first release whose flow API is typed for probatio; 2026.9
    ships probatio but still declares voluptuous schemas, so mypy fails there.
    The coordinator also looks a device up by identifier and config entry,
    which exists from 2026.8. Running against anything older has to fail here
    rather than in a type check nobody reads or at the first metadata update.
    """
    pytest.importorskip("homeassistant")
    import inspect

    from homeassistant.data_entry_flow import FlowHandler
    from homeassistant.helpers.device_registry import DeviceRegistry

    too_old = f"the installed Home Assistant is older than the claimed minimum {_claimed_minimum()}"
    schema_type = inspect.signature(FlowHandler.async_show_form).parameters["data_schema"]
    assert "probatio" in str(schema_type.annotation), too_old
    assert hasattr(DeviceRegistry, "async_get_device_by_identifier"), too_old


def test_every_tool_check_sh_runs_is_pinned_for_ci():
    """An unpinned tool turns CI red the day it ships a new rule.

    pytest itself is exempt: each lane's pinned phcc release brings its own.
    """
    script = (REPO / "check.sh").read_text(encoding="utf-8")
    tools = set(re.findall(r'"\$PYTHON" -m ([\w-]+)', script)) - {"pytest"}
    if "--cov" in script:
        tools.add("pytest-cov")
    pinned = {
        line.split("==")[0].strip()
        for line in (REPO / "requirements-ci.txt").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }
    assert tools, "found no tools in check.sh - the scan is broken"
    assert tools <= pinned, f"not pinned in requirements-ci.txt: {sorted(tools - pinned)}"
