# Getting help

Noust is maintained by one person and a few contributors. This page says where to ask, what makes a
report fixable in one pass, and what happens to an issue after you open it.

## Where to ask

| You want to | Go to |
|---|---|
| Report a **security** problem | A private advisory: <https://github.com/Perkybeet/noust/security/advisories/new>, or the email in [SECURITY.md](../SECURITY.md). Never a public issue. SECURITY.md says what happens next and when. |
| Report a **bug** | [A bug report](https://github.com/Perkybeet/noust/issues/new?template=bug_report.yml) |
| Ask for a **feature** | [A feature request](https://github.com/Perkybeet/noust/issues/new?template=feature_request.yml) |
| Ask for an **application in the catalogue** | [A recipe request](https://github.com/Perkybeet/noust/issues/new?template=recipe_request.yml), and [how to write one](contributing-recipes.md) |
| Ask **how to do something** | First [the documentation](../README.md), then [troubleshooting](troubleshooting.md). If it is not answered there, open an issue labelled `documentation`: the answer then goes into the page that should have had it. |
| Contribute a **change** | [CONTRIBUTING.md](../CONTRIBUTING.md) (the four rules, the architecture and how to run the checks). |

There is no chat room or forum yet. A community channel is a decision about how the project is
launched, and that decision has not been taken; until it is, an issue is the channel.

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
- **From the console**, Settings > About has the same report with a Copy button.
- For a problem with one application, add `noust diagnose DOMAIN`.
- `--output FILE` writes it to a file only you can read; `--json` prints it as data.

## What makes a report fixable

1. **The version and how Noust was installed.** The report has them; so does `noust --version`.
2. **What you did and what you expected.** The commands, in order.
3. **What happened instead, verbatim.** Paste the error as it appeared. Noust never paraphrases a
   system error (nginx's, systemd's, certbot's), because the exact words are usually the diagnosis.
4. **Whether it happens every time.**

## How issues are triaged

New issues get the `needs-triage` label from their template. The maintainer reads them and, in one
pass, gives each an area (`console`, `cli`, `deploy`, `web-server`, `databases`, `backups`, `fleet`,
`packaging`, `documentation`) and a verdict:

| Label | Meaning |
|---|---|
| `confirmed` | Reproduced, or clearly a defect from the report. It goes on the list of what is fixed next. |
| `needs-info` | Something is missing (the version, the support report, the steps). The issue waits for you; one that stays unanswered is closed, and reopens when you answer. |
| `duplicate` | Already reported; closed with a link to the original, where the discussion continues. |
| `wontfix` | Will not change, with the reason in the thread. A feature request that does not fit the product is closed this way, politely and with the reason. |
| `good first issue` | Small and well described; a good way into the code. |

The labels are defined in [`.github/labels.yml`](../.github/labels.yml). **There is no promise about
how fast the first answer comes**: Noust does not publish a response time it cannot keep. The only
timed commitment is for security reports, in [SECURITY.md](../SECURITY.md).

Fixes ship in the next patch release of each supported line (see the table in SECURITY.md). A
release does not change behaviour in a patch: [the release policy](release-policy.md) says what a
patch, a minor and a major version may contain.
