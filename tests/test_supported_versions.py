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


def _meets_minimum(installed: str, minimum: str) -> bool:
    from awesomeversion import AwesomeVersion

    return AwesomeVersion(installed) >= AwesomeVersion(minimum)


def test_the_installed_home_assistant_meets_the_claimed_minimum():
    """The minimum lane really runs the version hacs.json claims, or newer.

    Checking an API instead proved nothing: 2026.9 aliases voluptuous to
    probatio at runtime, so the probatio annotation the 2026.10 floor rests on
    already shows there - the difference only exists for mypy.
    """
    pytest.importorskip("homeassistant")
    from homeassistant.const import __version__

    assert _meets_minimum(__version__, _claimed_minimum()), (
        f"the installed Home Assistant {__version__} is older than the claimed "
        f"minimum {_claimed_minimum()}"
    )


def test_versions_compare_as_numbers():
    """2026.9 sorts after 2026.10 as text; the guard above must not."""
    pytest.importorskip("awesomeversion")
    assert not _meets_minimum("2026.9.4", "2026.10.0")
    assert _meets_minimum("2026.10.0", "2026.10.0")


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


GUARD_MODULES = ("test_supported_versions.py", "test_log_hygiene.py", "test_translations.py")


def test_every_guard_is_in_this_index():
    """Deleting a guard is a green diff; the index is what makes it visible."""
    index = (REPO / "tests" / "README.md").read_text(encoding="utf-8")
    listed = {
        entry
        for entry in re.findall(r"`(test_\w+\.py::test_\w+)`", index)
        if entry.split("::")[0] in GUARD_MODULES
    }
    present = set()
    for name in GUARD_MODULES:
        source = (REPO / "tests" / name).read_text(encoding="utf-8")
        present |= {f"{name}::{t}" for t in re.findall(r"^def (test_\w+)", source, flags=re.M)}
    assert not present - listed, f"not in tests/README.md: {sorted(present - listed)}"
    assert not listed - present, f"listed but gone: {sorted(listed - present)}"

