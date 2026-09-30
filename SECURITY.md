# Security policy

Noust runs as root and deploys code on the servers it manages, so a vulnerability in it is a
vulnerability in every one of them. Reports are welcome and handled as described here.

## Supported versions

| Version | Security fixes |
|---|---|
| 3.1.x | Yes |
| 3.0.x | Yes, until 3.2.0 is released |
| 2.x (WASM) and earlier | No: upgrade to 3.x (`docs/UPGRADING-3.0.md`) |

Fixes are released as a patch version of each supported line, published to PyPI, the openSUSE
Build Service repositories (Debian, Ubuntu, Fedora, openSUSE) and the container image
`ghcr.io/perkybeet/noust` at the same time.

## Reporting a vulnerability

Please report privately, never in a public issue, a pull request or a discussion:

- **GitHub**: open a private advisory at
  <https://github.com/Perkybeet/noust/security/advisories/new> (preferred: it keeps the
  report, the fix and the advisory in one place), or
- **email**: yago.lopez.adeje@gmail.com, with `[noust security]` in the subject.

Include what you can of: the Noust version (`noust --version`), the distribution, how Noust
was installed (package, pip, container), whether the console is exposed and how, the steps to
reproduce, and what an attacker gains. A proof of concept helps; exploitation of servers you do
not own does not.

## What happens next

| When | What |
|---|---|
| 3 working days | We acknowledge the report and say who is handling it. |
| 10 working days | We confirm (or explain why not) and agree on a severity (CVSS 3.1) with you. |
| 90 days at most | A fixed release is published. Critical issues are fixed as fast as they can be; if a fix needs longer we tell you why and agree a new date. |
| At release | A GitHub security advisory with the affected and fixed versions, a CVE when the issue warrants one, and credit to the reporter unless you prefer otherwise. |

We ask you to keep the issue confidential until the advisory is published (coordinated
disclosure), and we will not take legal action against research done in good faith within
this policy: on your own installation, without accessing others' data, and reported to us
first.

## Scope

In scope: the `noust` Python package and CLI, the console (the API under `/api`, the event
stream, the WebSockets and the React application), the fleet (tunnels, enrolment, the central),
the packages and the container image built from this repository.

Out of scope: vulnerabilities in the applications Noust deploys, in third-party software it
installs (nginx, certbot, database engines, Node.js, PHP), and findings that need root on the
server already (root is Noust's trust boundary: see `docs/security.md`, "Threat model").

## What a release carries

Every release is built by GitHub Actions from a signed tag, published to PyPI by trusted
publishing and to the OBS repositories with their signing key. Each GitHub release carries a
CycloneDX SBOM (`noust-<version>.cdx.json`: the Python dependencies and the npm packages the
console bundles), and CI runs `pip-audit` and `npm audit` on every change and weekly, with
Dependabot proposing dependency updates. See `.github/workflows/supply-chain.yml`, and
`.github/workflows/release.yml`, which writes the SBOM a release carries.

Organisations subject to Spain's Esquema Nacional de Seguridad will find how Noust maps to the
RD 311/2022 measures, and what remains theirs, in [docs/ENS.md](docs/ENS.md).
