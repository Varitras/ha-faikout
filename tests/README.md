# Guards

Deleting a guard is otherwise a green diff. Every guard that holds a rule
rather than testing a feature belongs in this list, with one line saying what
it holds.

| Guard | Holds |
|---|---|
| `test_supported_versions.py::test_minimum_version_is_the_one_a_ci_lane_installs` | hacs.json may only claim a minimum some CI lane really installs |
| `test_supported_versions.py::test_readme_states_the_same_minimum` | the README names the same minimum as hacs.json |
| `test_supported_versions.py::test_the_claimed_minimum_provides_what_the_code_relies_on` | that minimum really types its forms for probatio and has the device registry lookup the coordinator calls |
| `test_supported_versions.py::test_every_action_is_pinned_or_a_known_moving_ref` | every workflow action is version-pinned, bar two deliberate moving refs |
| `test_supported_versions.py::test_workflow_directory_is_not_empty` | the guard above actually found files to scan |
| `test_supported_versions.py::test_every_tool_check_sh_runs_is_pinned_for_ci` | every tool check.sh runs is pinned for CI, so a tool release cannot turn CI red unannounced |
| `test_log_hygiene.py::test_nothing_identifying_reaches_a_log_in_the_clear` | no logger call or exception message carries a host, MAC or topic unmasked |
| `test_log_hygiene.py::test_the_package_has_modules_to_scan` | the guard above actually found modules to scan |
| `test_log_hygiene.py::test_guard_resolves_the_assignment_in_force_at_the_raise` | the guard follows a variable to the value it holds at the raise |
| `test_translations.py::test_every_language_has_the_same_keys` | every language ships the same strings as English |
| `test_translations.py::test_every_raised_translation_key_exists_with_its_placeholders` | an error the code raises has its text and placeholders in every language |
| `test_translations.py::test_the_code_raises_translated_errors` | the guard above actually found raise sites to check |
| `conftest.py::no_deprecated_home_assistant_call` (autouse) | any call Home Assistant reports as deprecated fails the test that made it |

None of them may be pinned to a single file: they scan the workflow directory or
the whole package, so a new file is covered the day it arrives.

## Running everything

```sh
PYTHON=.venv/bin/python ./check.sh
```

The full suite needs Home Assistant's test machinery, which imports `fcntl` and
therefore does not run natively on Windows — use WSL2 there. Without it, the
Home Assistant tests skip themselves and the pure ones still run.

CI runs the same script, once per Home Assistant lane.

## The pre-push hook is local

`.git/hooks/pre-push` runs `check.sh`, a blocklist over the push range and
`gitleaks` over the whole history. It is not tracked, because it names this
machine's interpreter: **a fresh clone has no hook and must install one.**
The blocklist itself lives outside the repository, since several of its entries
are the very words it keeps out.
