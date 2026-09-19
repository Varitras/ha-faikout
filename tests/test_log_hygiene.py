"""Nothing that names the installation may reach a log record in the clear.

Home Assistant logs are the usual attachment to a bug report, so a module's
host name, the broker address and the topics carrying them have to be masked
before they are logged. That covers more than `_LOGGER` calls: the setup retry
and reauth paths in Home Assistant log the exception message, so an exception
raised there is a log line by another route. A behavioural test only covers
the one line being written at the time; this reads the package instead, so a
new call site is caught the day it appears.
"""
import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).parents[1] / "custom_components/faikout"

# Names that identify the installation rather than describe the problem.
IDENTIFYING = frozenset({"host", "_host", "topic", "device_id", "mac", "_mac"})

# Passing one of the above is fine as the argument of these.
MASKS = frozenset({"log_identifier", "masked_topic", "error_kind"})

LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})

# Exceptions whose message Home Assistant writes to its own log.
LOGGED_EXCEPTIONS = frozenset(
    {"ConfigEntryNotReady", "ConfigEntryAuthFailed", "HomeAssistantError"}
)


def _identifying_names(node: ast.AST) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in IDENTIFYING:
            found.add(child.attr)
        elif isinstance(child, ast.Name) and child.id in IDENTIFYING:
            found.add(child.id)
    return found


def _is_masked(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in MASKS
    )


def _reaches_a_log(call: ast.Call) -> bool:
    is_logger_call = (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in LOG_LEVELS
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_LOGGER"
    )
    is_logged_exception = (
        isinstance(call.func, ast.Name) and call.func.id in LOGGED_EXCEPTIONS
    )
    return is_logger_call or is_logged_exception


def _caught_exceptions(function: ast.AST) -> set[str]:
    """Names bound by `except ... as name`. Their text is somebody else's -
    a TLS failure spells out the hostname it rejected - so it may not be
    interpolated into anything that reaches a log."""
    return {
        node.name
        for node in ast.walk(function)
        if isinstance(node, ast.ExceptHandler) and node.name
    }


def _assignments(function: ast.AST) -> dict[str, list[ast.Assign]]:
    """Simple `name = value` assignments in a function, in source order."""
    values: dict[str, list[ast.Assign]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values.setdefault(target.id, []).append(node)
    return values


def _value_in_force(name: str, at: ast.Call, assignments) -> ast.AST | None:
    """The assignment a call actually sees: the nearest one above it. The
    last one in the function would let a harmless reassignment below the
    raise hide what was raised."""
    before = [node for node in assignments.get(name, ()) if node.lineno < at.lineno]
    return before[-1].value if before else None


def _message_parts(call: ast.Call, assignments):
    """The pieces of a call that reach the log, with f-strings and simple
    variables unfolded: a message built into a name first and raised after
    would otherwise hide what it interpolates."""
    is_exception = isinstance(call.func, ast.Name)
    arguments = call.args if is_exception else call.args[1:]
    for argument in arguments:
        if isinstance(argument, ast.Name):
            argument = _value_in_force(argument.id, call, assignments) or argument
        if isinstance(argument, ast.JoinedStr):
            for part in argument.values:
                if isinstance(part, ast.FormattedValue):
                    yield part.value
        else:
            yield argument


def _offenders(module: pathlib.Path) -> list[str]:
    return _offenders_of(module, module.read_text(encoding="utf-8"))


def _offenders_of(module: pathlib.Path, source: str) -> list[str]:
    tree = ast.parse(source)
    found = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assignments = _assignments(scope)
        caught = _caught_exceptions(scope)
        for call in ast.walk(scope):
            if not isinstance(call, ast.Call) or not _reaches_a_log(call):
                continue
            if any(keyword.arg == "exc_info" for keyword in call.keywords):
                # The traceback prints every message in the chain, ours and
                # the library's alike.
                found.append(f"{module.name}:{call.lineno} logs a traceback")
            for argument in _message_parts(call, assignments):
                if _is_masked(argument):
                    continue
                names = _identifying_names(argument)
                if names:
                    found.append(f"{module.name}:{call.lineno} passes {sorted(names)}")
                interpolated = {
                    node.id
                    for node in ast.walk(argument)
                    if isinstance(node, ast.Name) and node.id in caught
                }
                if interpolated:
                    found.append(
                        f"{module.name}:{call.lineno} interpolates {sorted(interpolated)}"
                    )
    return found


MODULES = sorted(PACKAGE.glob("*.py"))


def test_the_package_has_modules_to_scan():
    """A scan over nothing would pass quietly."""
    assert MODULES


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_nothing_identifying_reaches_a_log_in_the_clear(module):
    offenders = _offenders(module)
    assert not offenders, "mask what identifies the installation:\n" + "\n".join(offenders)


# --- the guard itself ---------------------------------------------------------
def _offenders_in(source: str) -> list[str]:
    path = pathlib.Path("snippet.py")
    return [line.split(" ", 1)[1] for line in _offenders_of(path, source)]


def test_guard_resolves_the_assignment_in_force_at_the_raise():
    """Last assignment in the function is not the one that matters; the one
    before the raise is. Reading the wrong one hides a leak behind a later,
    harmless reassignment."""
    leak_then_reassign = '''
def f(self):
    message = f"broker {self._host} refused"
    raise ConfigEntryNotReady(message)
    message = "harmless"
'''
    assert _offenders_in(leak_then_reassign) == ["passes ['_host']"]

    reassign_then_safe = '''
def f(self):
    message = f"broker {self._host} refused"
    message = "harmless"
    raise ConfigEntryNotReady(message)
'''
    assert _offenders_in(reassign_then_safe) == []
