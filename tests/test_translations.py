"""What the user reads has to exist in every language the integration ships.

A translation key the code raises with but the files lack shows the raw key in
the UI; a key only one language has shows English to the other.
"""
import ast
import json
import pathlib
import string

import pytest

PACKAGE = pathlib.Path(__file__).parents[1] / "custom_components" / "faikout"
LANGUAGES = sorted((PACKAGE / "translations").glob("*.json"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(tree, prefix=""):
    if not isinstance(tree, dict):
        return {prefix}
    return {key for name, sub in tree.items() for key in _keys(sub, f"{prefix}/{name}")}


def _raised_translations():
    """(translation_key, placeholder names) of every exception the code builds."""
    found = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            key = keywords.get("translation_key")
            # translation_domain marks an exception; entity descriptions carry
            # a translation_key too, but resolve it under "entity".
            if "translation_domain" not in keywords or not isinstance(key, ast.Constant):
                continue
            placeholders = keywords.get("translation_placeholders")
            names = set()
            if isinstance(placeholders, ast.Dict):
                names = {k.value for k in placeholders.keys if isinstance(k, ast.Constant)}
            found.append((f"{path.name}:{node.lineno}", key.value, names))
    return found


def _placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_language_has_the_same_keys():
    english = _keys(_load(PACKAGE / "translations" / "en.json"))
    for path in LANGUAGES:
        assert _keys(_load(path)) == english, f"{path.name} differs from en.json"


def test_the_code_raises_translated_errors():
    """The scan below is only worth as much as the sites it found."""
    assert len(_raised_translations()) >= 5


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.name)
def test_every_raised_translation_key_exists_with_its_placeholders(language):
    exceptions = _load(language).get("exceptions", {})
    for site, key, names in _raised_translations():
        assert key in exceptions, f"{site}: {key} missing from {language.name}"
        assert _placeholders(exceptions[key]["message"]) == names, (
            f"{site}: placeholders of {key} differ in {language.name}"
        )


def _assigned(path, name, inside=None):
    """The literal a module (or one of its classes) assigns to a name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes = [tree]
    if inside:
        scopes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == inside]
    for scope in scopes:
        for node in scope.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return ast.literal_eval(node.value)
    return None


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.name)
def test_the_quiet_fan_step_is_translated(language):
    """Home Assistant translates the usual fan modes, not the device's quiet step."""
    key = _assigned(PACKAGE / "climate.py", "_attr_translation_key", inside="FaikoutClimate")
    quiet = _assigned(PACKAGE / "const.py", "FAN_QUIET")
    assert key, "the climate entity has no translation key to hang the step on"
    states = _load(language)["entity"]["climate"][key]["state_attributes"]["fan_mode"]["state"]
    assert quiet in states
