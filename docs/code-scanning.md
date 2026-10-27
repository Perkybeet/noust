# Code scanning: every alert is decided

Noust is scanned on every push to `main`, on every pull request and every Wednesday
(`.github/workflows/security.yml`):

| Scanner | Reads | Reports to |
|---|---|---|
| CodeQL (`security-extended`) | the Python and the TypeScript | the repository's code scanning |
| Trivy | the lock files, the Dockerfiles and the central's container image | the repository's code scanning |
| gitleaks | the whole history of a change | the run itself (a leaked secret always blocks) |
| Dependency review | the dependencies a pull request adds | the pull request |

A finding is never left to age. It is **fixed**, or it is **dismissed in GitHub with the reason
written down**, and a job of the workflow fails while an alert of high or critical severity is open
on the branch (`scripts/code_scanning_gate.py`). That is what keeps a new alert from sitting under
the old ones: in October 2026 there were 434 open and none had ever been decided.

## Fixing

The cause is fixed in one place, not in the call that happened to be reported (rule 4 of
`CONTRIBUTING.md`):

- A name that becomes a path is judged where the path is made: `validators/`, `certificate_dir()`,
  `instance_layout.directory()`, `upload_tree()`.
- A name that becomes a part of a request to a platform's API is made one segment by
  `core.outbound.path_segment`, and every request is checked by `core.outbound.api_path` where it
  is sent.
- A value that reaches a log line is written out where the record is made (`core/logsafe.py`), so
  a line break in a domain cannot forge a line; the audit trail's bridge quotes every value so a
  space in one cannot name the actor.
- A Jinja environment is made once (`core/templates.py`).
- A name that becomes a directory of a certificate or a database instance is judged by the function
  that builds the directory, so a route that forgets to is still safe.

## Teaching CodeQL what Noust already does

CodeQL follows a value from a request to a file or a URL and only knows the standard library's and
the frameworks' ways of making it safe. When a value has been through a function of Noust that
returns it only when it is acceptable, the alert is about code that is already safe, and the way to
say so is a model, not a comment next to the line:

- `.github/codeql/extensions/noust-python/models/validators.model.yml` lists the functions that stop
  a value (`barrierModel`, kind `path-injection`). Nothing in the workflow loads it: the Python
  extractor puts the folder `.github/codeql/extensions` in the database's source archive and the
  analysis reads extension packs from there.
- `tests/test_codeql_models.py` holds every row to the code. A row names a function that must exist
  and must refuse a hostile name, and a function in the model without a proof there fails the test:
  a sanitizer that does not sanitize would hide a real alert.
- A row has to earn its place. Build the database (`codeql database create --language=python
  --build-mode=none`) of a tree **without** the pack, analyse it with the pack and without it
  (`--additional-packs=.github/codeql/extensions --model-packs=noust/python-models`), and keep a row
  only if the count falls without it. Clear the database's `db-python/default/cache` and `results`
  between runs: the evaluation cache outlives a change of model and gives the old answer.
- The access path of a row starts at an import (`Member[validators].Member[domain]...`), so it applies
  to calls from other modules. A function called by its own module's code is not reached.

## What the analysis leaves out

`.github/codeql/codeql-config.yml` keeps out of the analysis what is not Noust's code, each entry
with its reason: the console's committed build, the tests and their helpers (the console's
`*.test.ts(x)` too), the server the E2E suite runs against (`scripts/console_server.py`, which is
never installed), and the recipe catalogue, where `password: ""` is a value Noust generates.
`tests/test_codeql_config.py` fails on an entry that matches nothing. It also sets `py/log-injection`
aside, which is only valid while `tests/test_log_neutral.py` holds that every record is neutral.

A path is never added to quieten a finding in code that ships, and nothing is suppressed with a
comment in the code.

## Dismissing

An alert that is none of the above is dismissed in GitHub, never left open. The reason is one of
GitHub's three and the comment says why in a sentence:

| GitHub's reason | When | The comment says |
|---|---|---|
| False positive | The scanner reads something the code does not do | what actually happens, and what holds it (a test, a validator, the permission of the route) |
| Used in tests | The code is a fixture, an example or the harness | what it is for |
| Won't fix | The finding is true and accepted | what is accepted, why, and who has to be trusted for it to matter |

Two kinds of alert are accepted as they are:
a route that takes a directory of the server (adopting a stack, a bind mount, a dump to import)
needs a permission that is root-equivalent and, for the ones that change something, sudo mode; the
file system seam (`core/fs.py`) takes the path its caller already judged.

## What a pull request sees

A pull request is judged by its own check, not by the alerts of `main`. Turn on the **Code scanning
results** rule in the ruleset of `main` (Settings, Rules, Rulesets) with alerts of high or critical
severity as the threshold, so a merge that introduces one is refused before it is a decision to make.
