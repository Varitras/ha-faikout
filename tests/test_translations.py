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


# --- flow form errors and abort reasons ---------------------------------------
# The exception guard above never saw these: a key the options form shows with
# no text under options.error reaches the user as the raw key, in every language.
FLOW_SECTIONS = {
    "ConfigFlow": "config",
    "OptionsFlow": "options",
    "OptionsFlowWithReload": "options",
}
MAX_RESOLUTION = 5


def _functions(tree):
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _callee_name(node):
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _strings(node, scope, functions, depth=0, index=None):
    """Every key an expression can evaluate to. Fails rather than guesses.

    ``index`` follows one position of a returned tuple, as in
    ``_, error = await _probe_broker(...)``.
    """
    if isinstance(node, ast.Tuple) and index is not None:
        return _strings(node.elts[index], scope, functions, depth)
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, ast.IfExp):
        return _strings(node.body, scope, functions, depth) | _strings(
            node.orelse, scope, functions, depth
        )
    assert depth < MAX_RESOLUTION, f"cannot resolve {ast.dump(node)[:80]}"
    callee = _callee_name(node)
    if callee is not None:
        assert callee in functions, f"cannot resolve the call to {callee}"
        function = functions[callee]
        found = set()
        for ret in ast.walk(function):
            if isinstance(ret, ast.Return) and ret.value is not None:
                found |= _strings(ret.value, function, functions, depth + 1, index)
        return found
    assert isinstance(node, ast.Name), f"cannot resolve {ast.dump(node)[:80]}"
    found = set()
    resolved = False
    for assign in ast.walk(scope):
        if not isinstance(assign, ast.Assign):
            continue
        for target in assign.targets:
            if isinstance(target, ast.Name) and target.id == node.id:
                found |= _strings(assign.value, scope, functions, depth + 1, index)
                resolved = True
            elif isinstance(target, ast.Tuple):
                for position, element in enumerate(target.elts):
                    if isinstance(element, ast.Name) and element.id == node.id:
                        found |= _strings(assign.value, scope, functions, depth + 1, position)
                        resolved = True
    assert resolved, f"cannot resolve {node.id}"
    return found


def _flow_keys(source=None):
    """{section: {"error": keys, "abort": keys}} the flows can show."""
    if source is None:
        source = (PACKAGE / "config_flow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = _functions(tree)
    keys = {}
    for flow in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        bases = {b.id for b in flow.bases if isinstance(b, ast.Name)}
        section = next((FLOW_SECTIONS[b] for b in bases if b in FLOW_SECTIONS), None)
        if section is None:
            continue
        used = keys.setdefault(section, {"error": set(), "abort": set()})
        methods = [
            n for n in flow.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for method in methods:
            for node in ast.walk(method):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        is_error = (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "errors"
                        )
                        if is_error:
                            used["error"] |= _strings(node.value, method, functions)
                callee = _callee_name(node) if isinstance(node, ast.Call) else None
                unreadable = callee in ("AbortFlow", "_abort_if_unique_id_mismatch") or (
                    callee == "async_show_form"
                    and any(
                        k.arg == "errors"
                        and not (isinstance(k.value, ast.Name) and k.value.id == "errors")
                        for k in node.keywords
                    )
                )
                assert not unreadable, f"cannot read the keys of {callee} at line {node.lineno}"
                if callee == "async_abort":
                    for keyword in node.keywords:
                        if keyword.arg == "reason":
                            used["abort"] |= _strings(keyword.value, method, functions)
                if callee == "_abort_if_unique_id_configured":
                    used["abort"].add("already_configured")
                if callee == "async_update_reload_and_abort":
                    reasons = [k.value for k in node.keywords if k.arg == "reason"]
                    for reason in reasons:
                        used["abort"] |= _strings(reason, method, functions)
    return keys


def test_the_flows_show_keys_to_check():
    keys = _flow_keys()
    assert sum(len(v) for section in keys.values() for v in section.values()) >= 6


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.name)
def test_every_form_error_and_abort_reason_has_its_text(language):
    texts = _load(language)
    for section, kinds in _flow_keys().items():
        for kind, used in kinds.items():
            present = set(texts.get(section, {}).get(kind, {}))
            missing = sorted(used - present)
            assert not missing, f"{language.name} {section}.{kind} lacks {missing}"


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.name)
def test_no_form_error_or_abort_text_is_left_unused(language):
    texts = _load(language)
    keys = _flow_keys()
    for section in ("config", "options"):
        for kind in ("error", "abort"):
            present = set(texts.get(section, {}).get(kind, {}))
            used = keys.get(section, {}).get(kind, set())
            unused = sorted(present - used)
            assert not unused, f"{language.name} {section}.{kind} has unused {unused}"


UNREADABLE_FLOW_FORMS = {
    "AbortFlow": """
class F(ConfigFlow):
    async def async_step_user(self, user_input=None):
        raise AbortFlow("gone")
""",
    "unique id mismatch": """
class F(ConfigFlow):
    async def async_step_user(self, user_input=None):
        self._abort_if_unique_id_mismatch()
""",
    "errors under another name": """
class F(ConfigFlow):
    async def async_step_user(self, user_input=None):
        problems = {}
        problems["base"] = "nope"
        return self.async_show_form(step_id="user", errors=problems)
""",
    "inline errors": """
class F(ConfigFlow):
    async def async_step_user(self, user_input=None):
        return self.async_show_form(step_id="user", errors={"base": "nope"})
""",
}


@pytest.mark.parametrize("source", UNREADABLE_FLOW_FORMS.values(), ids=UNREADABLE_FLOW_FORMS.keys())
def test_the_flow_key_guard_refuses_forms_it_cannot_read(source):
    """A key it cannot see would pass silently; it has to stop instead."""
    with pytest.raises(AssertionError, match="cannot read"):
        _flow_keys(source)

