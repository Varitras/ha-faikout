# Guards

Deleting a guard is otherwise a green diff. Every guard that holds a rule
rather than testing a feature belongs in this list, with one line saying what
it holds.

| Guard | Holds |
|---|---|
| `test_supported_versions.py::test_minimum_version_is_the_one_a_ci_lane_installs` | hacs.json may only claim a minimum some CI lane really installs |
| `test_supported_versions.py::test_readme_states_the_same_minimum` | the README names the same minimum as hacs.json |
| `test_supported_versions.py::test_the_claimed_minimum_provides_the_registry_api_we_call` | that minimum really provides the device registry API the coordinator calls |
| `test_supported_versions.py::test_every_action_is_pinned_or_a_known_moving_ref` | every workflow action is version-pinned, bar two deliberate moving refs |
| `test_supported_versions.py::test_workflow_directory_is_not_empty` | the guard above actually found files to scan |

None of them may be pinned to a single file: they scan the workflow directory,
so a second workflow is covered the day it arrives.

## Running everything

```sh
PYTHON=~/ha-test/venv/bin/python ./check.sh
```

The full suite needs Home Assistant's test machinery, which imports `fcntl` and
therefore does not run natively on Windows — use WSL2 there. Without it, the
Home Assistant tests skip themselves and the pure ones still run.

## The pre-push hook is local

`.git/hooks/pre-push` runs `check.sh`, a blocklist over the push range and
`gitleaks` over the whole history. It is not tracked, because it names this
machine's interpreter: **a fresh clone has no hook and must install one.**
The blocklist itself lives outside the repository, since several of its entries
are the very words it keeps out.
