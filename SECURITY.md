# Security policy

Noust runs as root and deploys code on the servers it manages, so a vulnerability in it is a
vulnerability in every one of them. Reports are welcome and handled as described here.

## Supported versions

| Version | Security fixes |
|---|---|
| 4.0.x | Yes |
| 3.3.x | Yes, until 4.1.0 is released |
| 3.2.x and earlier | No: upgrade to a supported line |

Fixes are released as a patch version of each supported line, published to PyPI, the openSUSE
Build Service repositories (Debian, Ubuntu, Fedora, openSUSE) and the container image
`ghcr.io/perkybeet/noust` at the same time.

The two newest minor versions are supported. This table is kept by the release script
(`scripts/release.py`): releasing a new minor version adds its line as supported, keeps the
previous line until the minor version after the new one is released, and retires the line before
that. CI fails when the table does not list the version being released as supported.

## Reporting a vulnerability

Please report privately, never in a public issue, a pull request or a discussion:

- **GitHub**: open a private advisory at
  <https://github.com/Perkybeet/noust/security/advisories/new> (preferred: it keeps the
  report, the fix and the advisory in one place, and nobody but the maintainers sees it), or
- **email**: yago.lopez.adeje@gmail.com, with `[noust security]` in the subject.
  <!-- OWNER-TODO(security-address): when the project has a domain, create a dedicated
  address (security@<domain>), list it here and as a `Contact:` in
  .well-known/security.txt, and retire the personal one. `python scripts/release.py --check`
  fails if the two files disagree, so changing one reminds you of the other. -->

Include what you can of: the Noust version (`noust --version`), the distribution, how Noust
was installed (package, pip, container), whether the console is exposed and how, the steps to
reproduce, and what an attacker gains. A proof of concept helps; exploitation of servers you do
not own does not.

The same contacts, in the form tools read (RFC 9116), are in
[`.well-known/security.txt`](.well-known/security.txt). Each release renews its expiry; a file
that has lapsed means the project has stopped releasing, and its contacts should not be trusted
blindly.

### Encrypted reports

The advisory form is the encrypted channel: it is TLS to GitHub and the report is visible only
to the maintainers. Email is not encrypted. No PGP key is published yet, so please do not put
exploit details in an email: send the advisory, or send a short message asking for a channel.
<!-- OWNER-TODO(security-pgp): optional. Publish a key (`.well-known/pgp-key.txt`), print its
full 40-hex fingerprint below this paragraph and add `Encryption:` to security.txt; the release
check refuses a key without a fingerprint. -->

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

Every release is built by GitHub Actions from its tag; from 4.0.0 on the tag is signed with the
maintainer's SSH key, which GitHub shows as verified (earlier tags are not signed). What a release
publishes can be checked by someone who does not trust whoever uploaded it:

| What | Where | How it is protected |
|---|---|---|
| The sdist, wheel and Debian packages | The GitHub release | `noust-<version>.SHA256SUMS` with a keyless cosign signature (`.sig`, `.pem`), and a GitHub build-provenance attestation for each file |
| The CycloneDX SBOM (`noust-<version>.cdx.json`: the Python dependencies and the npm packages the console bundles) | The GitHub release | Attested for the wheel, and covered by the checksums |
| The installer (`install.sh`) | The GitHub release | `SHA256SUMS` signed keyless: see [docs/PLATFORMS.md](docs/PLATFORMS.md#verifying-the-installer) |
| `noust` on PyPI | PyPI | Trusted publishing, with PEP 740 attestations; the wheel and the sdist are the same bytes as the release's, and can be rebuilt from the tag ([docs/reproducible-builds.md](docs/reproducible-builds.md)) |
| The Debian, Ubuntu, Fedora and openSUSE repositories | OBS | The repository's signing key |
| The central's image `ghcr.io/perkybeet/noust` | GHCR | Signed by digest (keyless cosign), with build provenance and an SBOM attested to it; a release is held back while the image has a critical vulnerability that is already fixed |

The commands that check each of them are in
[docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md#verifying-a-release). CI also runs CodeQL,
gitleaks, Trivy, `pip-audit` and `npm audit`, and Dependabot proposes dependency updates: see
`.github/workflows/security.yml` and `.github/workflows/supply-chain.yml`.

A release that fixes a vulnerability is titled *(security release)* on GitHub, and an
installation that is behind one says so: in the banner of the CLI, in `noust health`, and in the
console under Settings > About, with the advisory when it has one.

Organisations subject to Spain's Esquema Nacional de Seguridad will find how Noust maps to the
RD 311/2022 measures, and what remains theirs, in [docs/ENS.md](docs/ENS.md).

## Acknowledgments

People who reported a vulnerability and agreed to be named are listed here and in the advisory
that fixes it. The list is empty so far. A report is credited by default; say so if you would
rather not be.
