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
MASKS = frozenset({"log_identifier", "masked_topic"})

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


def _assignments(function: ast.AST) -> dict[str, ast.AST]:
    """Simple `name = value` assignments in a function, last one wins."""
    values: dict[str, ast.AST] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value
    return values


def _message_parts(call: ast.Call, assignments: dict[str, ast.AST]):
    """The pieces of a call that reach the log, with f-strings and simple
    variables unfolded: a message built into a name first and raised after
    would otherwise hide what it interpolates."""
    is_exception = isinstance(call.func, ast.Name)
    arguments = call.args if is_exception else call.args[1:]
    for argument in arguments:
        if isinstance(argument, ast.Name) and argument.id in assignments:
            argument = assignments[argument.id]
        if isinstance(argument, ast.JoinedStr):
            for part in argument.values:
                if isinstance(part, ast.FormattedValue):
                    yield part.value
        else:
            yield argument


def _offenders(module: pathlib.Path) -> list[str]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    found = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assignments = _assignments(scope)
        for call in ast.walk(scope):
            if not isinstance(call, ast.Call) or not _reaches_a_log(call):
                continue
            for argument in _message_parts(call, assignments):
                names = _identifying_names(argument)
                if names and not _is_masked(argument):
                    found.append(f"{module.name}:{call.lineno} passes {sorted(names)}")
    return found


MODULES = sorted(PACKAGE.glob("*.py"))


def test_the_package_has_modules_to_scan():
    """A scan over nothing would pass quietly."""
    assert MODULES


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_nothing_identifying_reaches_a_log_in_the_clear(module):
    offenders = _offenders(module)
    assert not offenders, "mask what identifies the installation:\n" + "\n".join(offenders)
