# Guards

Deleting a guard is otherwise a green diff. Every guard that holds a rule
rather than testing a feature belongs in this list, with one line saying what
it holds.

| Guard | Holds |
|---|---|
| `test_supported_versions.py::test_minimum_version_is_the_one_a_ci_lane_installs` | hacs.json may only claim a minimum some CI lane really installs |
| `test_supported_versions.py::test_readme_states_the_same_minimum` | the README names the same minimum as hacs.json |
| `test_supported_versions.py::test_the_installed_home_assistant_meets_the_claimed_minimum` | the Home Assistant a lane installs is at least the claimed minimum |
| `test_supported_versions.py::test_versions_compare_as_numbers` | the guard above compares versions as numbers, not text |
| `test_supported_versions.py::test_every_action_is_pinned_or_a_known_moving_ref` | every workflow action is version-pinned, bar two deliberate moving refs |
| `test_supported_versions.py::test_workflow_directory_is_not_empty` | the guard above actually found files to scan |
| `test_supported_versions.py::test_every_tool_check_sh_runs_is_pinned_for_ci` | every tool check.sh runs is pinned for CI, so a tool release cannot turn CI red unannounced |
| `test_log_hygiene.py::test_nothing_identifying_reaches_a_log_in_the_clear` | no logger call or exception message carries a host, MAC or topic unmasked |
| `test_log_hygiene.py::test_the_package_has_modules_to_scan` | the guard above actually found modules to scan |
| `test_log_hygiene.py::test_guard_resolves_the_assignment_in_force_at_the_raise` | the guard follows a variable to the value it holds at the raise |
| `test_log_hygiene.py::test_guard_reports_each_form_it_once_missed` | every logging and raising form the guard once missed stays caught |
| `test_log_hygiene.py::test_guard_accepts_masked_values_in_every_form` | the counter-check: masked values in those forms pass |
| `test_translations.py::test_every_language_has_the_same_keys` | every language ships the same strings as English |
| `test_translations.py::test_every_raised_translation_key_exists_with_its_placeholders` | an error the code raises has its text and placeholders in every language |
| `test_translations.py::test_the_code_raises_translated_errors` | the guard above actually found raise sites to check |
| `test_translations.py::test_the_quiet_fan_step_is_translated` | the device's own fan step has a name in every language |
| `test_translations.py::test_every_form_error_and_abort_reason_has_its_text` | every error and abort reason a flow shows has its text in every language |
| `test_translations.py::test_no_form_error_or_abort_text_is_left_unused` | no flow error or abort text outlives the code that showed it |
| `test_translations.py::test_the_flows_show_keys_to_check` | the two guards above actually found flow keys to check |
| `test_supported_versions.py::test_every_guard_is_in_this_index` | every test in the guard modules is listed here |
| `conftest.py::no_deprecated_home_assistant_call` (autouse) | any call Home Assistant reports as deprecated fails the test that made it |

Guards over files that multiply - workflows, package modules, translations - scan
the whole directory, so a new file is covered the day it arrives. The rest read
one named file each (README, hacs.json, check.sh, requirements-ci.txt), or the
installed Home Assistant.

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
