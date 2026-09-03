"""The version claims outside the code have to agree with what CI proves.

Dependabot keeps the actions current, but it never reads a claim: the minimum
Home Assistant version lives in hacs.json, in the README and in the CI lane
that actually installs it, and nothing stops those three from drifting apart.
A claimed minimum no lane installs is a promise nobody checks.
"""
import json
import pathlib
import re

REPO = pathlib.Path(__file__).parents[1]
WORKFLOW = (REPO / ".github/workflows/tests.yml").read_text(encoding="utf-8")

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


def test_every_action_is_pinned_or_a_known_moving_ref():
    """A new action added on a branch would silently change under us."""
    unpinned = [
        used
        for used in re.findall(r"uses: (\S+)", WORKFLOW)
        if used not in MOVING_REFS and not re.search(r"@(v\d|[0-9a-f]{40})", used)
    ]
    assert not unpinned, f"not pinned to a version or an allowed moving ref: {unpinned}"
