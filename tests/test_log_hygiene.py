"""Nothing that names the installation may be logged in the clear.

Home Assistant logs are the usual attachment to a bug report, so a module's
host name, the broker address and the topics carrying them have to be masked
before they reach a log record. A behavioural test only ever covers the one
line being written at the time; this reads the package instead, so a new call
site is caught the day it appears.
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


def _logger_calls(tree: ast.AST):
    for node in ast.walk(tree):
        is_logger_call = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_LEVELS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_LOGGER"
        )
        if is_logger_call:
            yield node


MODULES = sorted(PACKAGE.glob("*.py"))


def test_the_package_has_modules_to_scan():
    """A scan over nothing would pass quietly."""
    assert MODULES


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_no_log_call_passes_an_identifier_in_the_clear(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offenders = [
        f"{module.name}:{call.lineno} passes {sorted(names)}"
        for call in _logger_calls(tree)
        for argument in call.args[1:]
        if (names := _identifying_names(argument)) and not _is_masked(argument)
    ]
    assert not offenders, "log calls must mask what identifies the installation:\n" + (
        "\n".join(offenders)
    )
