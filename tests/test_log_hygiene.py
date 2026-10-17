"""Nothing that names the installation may reach a log record in the clear.

Home Assistant logs are the usual attachment to a bug report, so a module's
host name, the broker address and the topics carrying them have to be masked
before they are logged. That covers more than `_LOGGER` calls: the setup retry
and reauth paths in Home Assistant log the exception message, so an exception
raised there is a log line by another route. A behavioural test only covers
the one line being written at the time; this reads the package instead.

What it reads: logger calls (any logger), every raised exception and every
call to a class named like one, their positional and keyword arguments,
f-strings, dict values and the variables they came from, and how a caught
exception is passed on or chained. What it does not tell apart: the branches
of an if/else (the nearest assignment above wins), class bodies, and a
catch-all handler that cleans up and re-raises.
"""
import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).parents[1] / "custom_components/faikout"

# Names that identify the installation rather than describe the problem, or
# carry what the device sent - untrusted text that can hold an address or a
# network name.
IDENTIFYING = frozenset(
    {
        "host",
        "_host",
        "topic",
        "device_id",
        "mac",
        "_mac",
        "title",
        "username",
        "password",
        "unique_id",
        "payload",
        "device_meta",
        "hosts",
    }
)

# The same values read out of a config entry by key.
IDENTIFYING_KEYS = frozenset(
    {
        "CONF_HOST",
        "CONF_MAC",
        "CONF_DEVICE_ID",
        "CONF_MQTT_HOST",
        "CONF_MQTT_USERNAME",
        "CONF_MQTT_PASSWORD",
    }
)

# The values behind those keys, for a key written out as a string.
IDENTIFYING_KEY_VALUES = frozenset(
    {"host", "mac", "device_id", "mqtt_host", "mqtt_username", "mqtt_password"}
)

# Passing one of the above is fine as the argument of these; a length says
# nothing about the content.
MASKS = frozenset({"log_identifier", "masked_topic", "error_kind", "len"})

LOG_LEVELS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
)

# Home Assistant writes the message of the exceptions an integration raises to
# its own log, whatever the class. These name endings catch them when they are
# built without being raised on the spot.
EXCEPTION_ENDINGS = ("Error", "NotReady", "AuthFailed", "Failed", "Exception")

CATCH_ALL = frozenset({"Exception", "BaseException"})

# Keywords that say how to translate or log, not what.
NOT_MESSAGE = frozenset(
    {"translation_domain", "translation_key", "exc_info", "stack_info", "stacklevel"}
)

# How far a chain of `a = ...; b = a` is followed.
MAX_INDIRECTION = 5


def _identifying_key(node: ast.AST) -> str | None:
    """CONF_HOST, or "host" written out: a lookup key naming identifying data."""
    if isinstance(node, ast.Name) and node.id in IDENTIFYING_KEYS:
        return node.id
    if isinstance(node, ast.Constant) and node.value in IDENTIFYING_KEY_VALUES:
        return str(node.value)
    return None


def _identifying_names(node: ast.AST) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in IDENTIFYING:
            found.add(child.attr)
        elif isinstance(child, ast.Name) and child.id in IDENTIFYING:
            found.add(child.id)
        elif isinstance(child, ast.Subscript) and _identifying_key(child.slice):
            found.add(_identifying_key(child.slice))
        elif (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "get"
            and child.args
            and _identifying_key(child.args[0])
        ):
            found.add(_identifying_key(child.args[0]))
    return found


def _is_masked(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in MASKS
    )


def _callee(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _is_logger_call(call: ast.Call) -> bool:
    """Any logger, whatever it is called: _LOGGER, logger, self.logger, logging."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in LOG_LEVELS:
        return False
    receiver = call.func.value
    if isinstance(receiver, ast.Call):
        return _callee(receiver) == "getLogger"
    if isinstance(receiver, ast.Attribute):
        return "log" in receiver.attr.lower()
    return isinstance(receiver, ast.Name) and "log" in receiver.id.lower()


def _raised_calls(scope: ast.AST) -> set[int]:
    return {
        id(node.exc)
        for node in ast.walk(scope)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
    }


def _caught_exceptions(function: ast.AST) -> set[str]:
    """Names bound by `except ... as name`. Their text is somebody else's -
    a TLS failure spells out the hostname it rejected - so it may not be
    interpolated into anything that reaches a log."""
    return {
        node.name
        for node in ast.walk(function)
        if isinstance(node, ast.ExceptHandler) and node.name
    }


def _assignments(scope: ast.AST) -> dict[str, list[tuple[int, ast.AST]]]:
    """`name = value` in a scope, in source order, as (line, value).

    Tuple targets are paired element by element (`device, reason = a, b`);
    unpacking anything else gives every name the whole value.
    """
    values: dict[str, list[tuple[int, ast.AST]]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            pairs = []
            for target in node.targets:
                if isinstance(target, ast.Name):
                    pairs.append((target, node.value))
                elif isinstance(target, (ast.Tuple, ast.List)):
                    same_shape = isinstance(node.value, (ast.Tuple, ast.List)) and len(
                        node.value.elts
                    ) == len(target.elts)
                    for position, element in enumerate(target.elts):
                        value = node.value.elts[position] if same_shape else node.value
                        pairs.append((element, value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        else:
            continue
        for target, value in pairs:
            if isinstance(target, ast.Name):
                values.setdefault(target.id, []).append((node.lineno, value))
    return values


def _value_in_force(name: str, at: ast.AST, assignments) -> ast.AST | None:
    """The assignment a call actually sees: the nearest one above it. The
    last one in the function would let a harmless reassignment below the
    raise hide what was raised."""
    before = [value for line, value in assignments.get(name, ()) if line < at.lineno]
    return before[-1] if before else None


def _leaves(node: ast.AST, at: ast.AST, assignments, depth: int = 0):
    """What a message argument really carries: f-strings, dict values and
    variables unfolded, so a value built up before the call cannot hide."""
    if isinstance(node, ast.Name) and depth < MAX_INDIRECTION:
        value = _value_in_force(node.id, at, assignments)
        if value is not None:
            yield from _leaves(value, at, assignments, depth + 1)
            return
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                yield from _leaves(part.value, at, assignments, depth)
        return
    if isinstance(node, ast.Dict):
        for value in node.values:
            yield from _leaves(value, at, assignments, depth)
        return
    yield node


def _message_parts(call: ast.Call, assignments):
    arguments = [*call.args]
    arguments += [kw.value for kw in call.keywords if kw.arg not in NOT_MESSAGE]
    for argument in arguments:
        yield from _leaves(argument, call, assignments)


def _handler_names(handler: ast.ExceptHandler) -> list[str]:
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = []
    for node in types:
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
    return names


def _is_own_domain_check(test: ast.AST) -> bool:
    """`<x>.translation_domain == DOMAIN`, alone or as one part of an `and`."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_is_own_domain_check(value) for value in test.values)
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    sides = [test.left, test.comparators[0]]
    has_domain = any(isinstance(s, ast.Name) and s.id == "DOMAIN" for s in sides)
    has_origin = any(
        isinstance(s, ast.Attribute) and s.attr == "translation_domain" for s in sides
    )
    return has_domain and has_origin


def _passed_on(handler: ast.ExceptHandler) -> list[ast.Raise]:
    """Raises that hand the caught exception on as it is, unless an `if` on its
    translation_domain only lets the integration's own, masked ones through."""
    found = []

    def visit(nodes, vetted):
        for node in nodes:
            if isinstance(node, ast.If):
                checks_origin = _is_own_domain_check(node.test)
                visit(node.body, vetted or checks_origin)
                visit(node.orelse, vetted)
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            is_pass_on = isinstance(node, ast.Raise) and (
                node.exc is None
                or (isinstance(node.exc, ast.Name) and node.exc.id == handler.name)
            )
            if is_pass_on and not vetted:
                found.append(node)
            for field in ("body", "orelse", "finalbody", "handlers"):
                visit(getattr(node, field, []), vetted)

    visit(handler.body, False)
    return found


def _offenders(module: pathlib.Path) -> list[str]:
    return _offenders_of(module, module.read_text(encoding="utf-8"))


def _module_level(tree: ast.Module) -> ast.Module:
    """The module with its function bodies left out, scanned as one scope."""
    body = [
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return ast.Module(body=body, type_ignores=[])


def _offenders_of(module: pathlib.Path, source: str) -> list[str]:
    tree = ast.parse(source)
    scopes = [_module_level(tree)] + [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    found = []
    for scope in scopes:
        assignments = _assignments(scope)
        caught = _caught_exceptions(scope)
        raised = _raised_calls(scope)
        for node in ast.walk(scope):
            # A catch-all that cleans up and re-raises forwards whatever the
            # setup raised, and every raise site is checked on its own. Picking
            # out a specific foreign error to hand on is what leaks its text.
            # shortcut: an `except Exception: raise` in a send path passes too.
            picks_foreign = isinstance(node, ast.ExceptHandler) and any(
                name.endswith(EXCEPTION_ENDINGS) and name not in CATCH_ALL
                for name in _handler_names(node)
            )
            if picks_foreign:
                for raise_node in _passed_on(node):
                    found.append(
                        f"{module.name}:{raise_node.lineno} passes a caught exception on as it is"
                    )
            if (
                isinstance(node, ast.Raise)
                and isinstance(node.cause, ast.Name)
                and node.cause.id in caught
            ):
                # Logged tracebacks print the cause in full: the library's text.
                found.append(f"{module.name}:{node.lineno} chains {node.cause.id} as the cause")
            if isinstance(node, ast.ExceptHandler):
                for raise_node in ast.walk(node):
                    # Without `from`, the caught exception rides along as the
                    # context and a traceback prints it just the same.
                    keeps_context = (
                        isinstance(raise_node, ast.Raise)
                        and raise_node.exc is not None
                        and raise_node.cause is None
                        and not (
                            isinstance(raise_node.exc, ast.Name)
                            and raise_node.exc.id == node.name
                        )
                    )
                    if keeps_context:
                        line = raise_node.lineno
                        found.append(f"{module.name}:{line} keeps the caught exception as context")
        for call in ast.walk(scope):
            if not isinstance(call, ast.Call):
                continue
            is_logger = _is_logger_call(call)
            is_exception = id(call) in raised or _callee(call).endswith(EXCEPTION_ENDINGS)
            if not (is_logger or is_exception):
                continue
            traceback = is_logger and (
                _callee(call) == "exception"
                or any(keyword.arg == "exc_info" for keyword in call.keywords)
            )
            if traceback:
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


# One snippet per form the guard once missed; each must be reported.
LEAKS = {
    "f-string message": '''
def f(self):
    _LOGGER.warning(f"lost {self.host}")
''',
    "traceback via exception()": '''
def f(self):
    _LOGGER.exception("failed")
''',
    "logger under another name": '''
def f(self):
    logger.info("lost %s", self.host)
''',
    "logger on an attribute": '''
def f(self):
    self.logger.info("lost %s", self.host)
''',
    "translation placeholder": '''
def f(self):
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="k",
        translation_placeholders={"device": self.host},
    )
''',
    "another exception class": '''
def f(self):
    raise UpdateFailed(f"no answer from {self.host}")
''',
    "exception through a module": '''
def f(self):
    raise exceptions.ConfigEntryError(f"no answer from {self.host}")
''',
    "exception class held in a variable": '''
def f(self):
    error_class = ConfigEntryNotReady
    raise error_class(f"no answer from {self.host}")
''',
    "entry title": '''
def f(entry):
    raise HomeAssistantError(f"setup of {entry.title} failed")
''',
    "host read by config key": '''
def f(entry):
    _LOGGER.debug("connecting to %s", entry.data[CONF_HOST])
''',
    "password": '''
def f(password):
    _LOGGER.debug("trying %s", password)
''',
    "two-step variable": '''
def f(self):
    name = self.host
    message = f"lost {name}"
    raise HomeAssistantError(message)
''',
    "module level": '''
_LOGGER.info("loaded for %s", host)
''',
    "tuple assignment": '''
def f(self):
    device, reason = self.host, "x"
    raise HomeAssistantError(f"{device}: {reason}")
''',
    "annotated assignment": '''
def f(self):
    device: str = self.host
    raise HomeAssistantError(device)
''',
    "config key read with get": '''
def f(entry):
    _LOGGER.debug("connecting to %s", entry.data.get(CONF_HOST))
''',
    "config key as a string": '''
def f(entry):
    _LOGGER.debug("connecting to %s", entry.data["host"])
''',
    "unique id": '''
def f(self):
    _LOGGER.debug("set up %s", self.unique_id)
''',
    "device payload": '''
def f(msg):
    _LOGGER.warning("bad state %s", msg.payload)
''',
    "logger from getLogger": '''
def f(self):
    logging.getLogger(__name__).warning("lost %s", self.host)
''',
    "log() with a level": '''
def f(self):
    _LOGGER.log(30, "lost %s", self.host)
''',
    "foreign error passed on": '''
def f(self):
    try:
        publish()
    except HomeAssistantError:
        raise
''',
    "foreign error chained as the cause": '''
def f(self):
    try:
        connect()
    except OSError as err:
        raise ConfigEntryNotReady(error_kind(err)) from err
''',
    "foreign error kept as context": '''
def f(self):
    try:
        connect()
    except OSError as err:
        raise ConfigEntryNotReady(error_kind(err))
''',
    "origin check inverted": '''
def f(self):
    try:
        publish()
    except HomeAssistantError as err:
        if err.translation_domain != DOMAIN:
            raise
''',
    "origin check that only tests presence": '''
def f(self):
    try:
        publish()
    except HomeAssistantError as err:
        if err.translation_domain:
            raise
''',
}


@pytest.mark.parametrize("source", LEAKS.values(), ids=LEAKS.keys())
def test_guard_reports_each_form_it_once_missed(source):
    assert _offenders_in(source)


def test_guard_accepts_masked_values_in_every_form():
    """The counter-check: the same forms, masked, are fine."""
    masked = '''
def f(self, entry, err):
    _LOGGER.warning(f"lost {log_identifier(self.host)}")
    logger.info("lost %s", masked_topic(self.topic))
    device = log_identifier(self.host)
    raise HomeAssistantError(
        f"no answer from {device}: {error_kind(err)}",
        translation_domain=DOMAIN,
        translation_key="k",
        translation_placeholders={"device": device, "reason": error_kind(err)},
    )

def g(self, payload):
    device, reason = log_identifier(self.host), "x"
    _LOGGER.warning("ignoring %d characters from %s", len(payload), device)
    try:
        publish()
    except HomeAssistantError as err:
        if err.translation_domain == DOMAIN:
            raise
        raise HomeAssistantError(error_kind(err)) from None
'''
    assert _offenders_in(masked) == []
