# Getting help

Noust is maintained by one person and a few contributors. This page says where to ask, what makes a
report fixable in one pass, and what happens to an issue after you open it.

## Where to ask

| You want to | Go to |
|---|---|
| Report a **security** problem | A private advisory (the repository's Security tab, "Report a vulnerability"): <https://github.com/Perkybeet/noust/security/advisories/new>, or the email in [SECURITY.md](../SECURITY.md). Never a public issue. SECURITY.md says what happens next and when. |
| Report a **problem from the console** | **Report a problem** in the console's help menu, or on its error screen: it opens the form with what it knows filled in, and [what to do with the file it gives you](#reporting-from-the-console) is below. |
| Report a **bug** | [A bug report](https://github.com/Perkybeet/noust/issues/new?template=bug_report.yml) |
| Ask for a **feature** | [A feature request](https://github.com/Perkybeet/noust/issues/new?template=feature_request.yml) |
| Ask for an **application in the catalogue** | [A recipe request](https://github.com/Perkybeet/noust/issues/new?template=recipe_request.yml), and [how to write one](contributing-recipes.md) |
| Ask **how to do something**, or talk an idea over | [Discussions](https://github.com/Perkybeet/noust/discussions): [Q&A](https://github.com/Perkybeet/noust/discussions/categories/q-a) for a question, [Ideas](https://github.com/Perkybeet/noust/discussions/categories/ideas) for something that is not yet a request. Read [the documentation](../README.md) and [troubleshooting](troubleshooting.md) first. An answer that belongs in a page of the documentation ends up there. |
| Contribute a **change** | [CONTRIBUTING.md](../CONTRIBUTING.md): the four rules, the architecture and how to run the checks. The first pull request signs [the contributor agreement](../CLA.md) with one comment. Everyone who takes part follows the [code of conduct](../CODE_OF_CONDUCT.md). |

There is no chat room. A community channel beyond Discussions is a decision about how the project is
launched, and that decision has not been taken.

## Reporting from the console

**Report a problem** (the help menu, and the screen that shows when the console itself fails) is the
quickest way to a report a maintainer can act on in one pass. It asks what happened and the steps,
lets you add images (choose them, paste them, or capture the tab when the browser allows it) and shows
you exactly what it will put in the file before you take it. Then:

1. The console opens [the form](https://github.com/Perkybeet/noust/issues/new?template=console_report.yml)
   in a new tab with the title, the version, the area, what happened and the steps already filled in.
2. It downloads one file with the support report (the one `noust support report` prints, described
   [below](#what-to-include), with secrets masked), what only the browser knows (the page you were
   on, the console's version, the browser, the last errors the console logged, masked the same way)
   and your images.
3. You read the file, drag it into the form's last box, wait for its link to appear and submit.

Nothing is sent by the console itself: it prepares a page and a file, and nothing is public until you
post. The issue gets the labels `bug`, `from-console` and `needs-triage` by itself, and is triaged like
any other.

## What to include

Run this on the server, as root, and paste its output into the report:

```bash
noust support report
```

It collects what a maintainer would otherwise ask for one question at a time: the Noust version and
how it was installed, the distribution and where it stands against [the platforms Noust
supports](PLATFORMS.md), the health report, every application's state, the diagnosis of the ones
that need attention, the Noust units and the last lines of the console's journal.

- **Secrets are masked before anything is printed.** Noust masks every secret it knows (its own
  configuration, each application's `.env`) and anything shaped like one (a token, a private key, an
  `Authorization` header, the password in a connection string, the value of a `NAME=value` whose name
  says it is secret). Read it before you post it anyway: a report is a copy of your server.
- **Domains and the host's name are included.** `noust support report --hide-domains` replaces them
  with `app-1`, `app-2` and `host` through the whole document, consistently.
- **From the console**, Settings > About Noust has the same report with a Copy button.
- For a problem with one application, add `noust diagnose DOMAIN`.
- `--output FILE` writes it to a file only you can read; `--json` prints it as data.

## What makes a report fixable

1. **The version and how Noust was installed.** The report has them; so does `noust --version`.
2. **What you did and what you expected.** The commands, in order.
3. **What happened instead, verbatim.** Paste the error as it appeared. Noust never paraphrases a
   system error (nginx's, systemd's, certbot's), because the exact words are usually the diagnosis.
4. **Whether it happens every time.**

## How issues are triaged

Every form applies its labels by itself: the type (`bug` or `enhancement`) and `needs-triage`, and
`from-console` on a report that came from the console. The maintainer reads new issues and, in one
pass, gives each an area and a verdict.

The areas say which part of the product it is about: `area/apps`, `area/databases`, `area/domains`,
`area/network`, `area/server`, `area/security`, `area/backups`, `area/console`, `area/fleet`,
`area/cli`, `area/docs`, `area/packaging` and `area/recipes`. The form's "Where does it happen" is the
reporter's guess at one of them; the label is the maintainer's.

| Label | Meaning |
|---|---|
| `needs-triage` | Not looked at yet. Removed when the issue has an area and a verdict. |
| `confirmed` | Reproduced, or clearly a defect from the report. It goes on the list of what is fixed next. |
| `needs-info` | Something is missing (the version, the support report, the steps). The issue waits for you; one that stays unanswered is closed, and reopens when you answer. |
| `duplicate` | Already reported; closed with a link to the original, where the discussion continues. |
| `wontfix` | Will not change, with the reason in the thread. A feature request that does not fit the product is closed this way, politely and with the reason. |
| `question` | A question that is not a defect; it moves to Discussions. |
| `from-console` | Opened from the console's **Report a problem**, with its report file attached. |
| `good first issue` | Small and well described; a good way into the code. |
| `help wanted` | A change the maintainer would welcome from someone else. |

The labels are defined in [`.github/labels.yml`](../.github/labels.yml). **There is no promise about
how fast the first answer comes**: Noust does not publish a response time it cannot keep. The only
timed commitment is for security reports, in [SECURITY.md](../SECURITY.md).

Fixes ship in the next patch release of each supported line (see the table in SECURITY.md). A
release does not change behaviour in a patch: [the release policy](release-policy.md) says what a
patch, a minor and a major version may contain.
