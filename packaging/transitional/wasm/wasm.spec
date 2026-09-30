# spec file for package wasm-cli, now a transitional package
#
# Copyright (c) 2024-2026 Yago López Prado
# License: AGPL-3.0-or-later
#
# WASM was renamed to Noust in 3.0.0 and its RPM, wasm-cli, became this: an
# empty package that Requires noust, so `dnf upgrade` and `zypper up` bring
# noust in as an ordinary upgrade of wasm-cli. It is built in the OBS package
# home:Perkybeet/wasm, from packaging/transitional/wasm in the noust repository.
#
# noust.spec declares Conflicts: wasm-cli < 3.0.0~ rather than Obsoletes, which
# is what makes the solver pick this upgrade: see the comment there. What rpm
# does to /etc/wasm/config.yaml while it removes wasm-cli 2.x is handled by
# noust's %%pre and %%posttrans, which run on either path.

Name:           wasm-cli
Version:        3.1.10
Release:        1%{?dist}
Summary:        Transitional package: WASM is now Noust
License:        AGPL-3.0-or-later
URL:            https://github.com/Perkybeet/noust
Source0:        wasm-%{version}.tar.gz
BuildArch:      noarch
Requires:       noust >= %{version}

%description
WASM was renamed to Noust in 3.0.0. This empty package only requires noust,
so that upgrading a server that has wasm-cli installed brings noust in.

The command is noust; wasm remains an alias for the whole 3.x series. This
package can be removed once noust is installed; with dnf, run
`dnf mark user noust` first, or dnf removes noust along with it.

%prep
%setup -q -n wasm-%{version}

%build

%install

%files
%license LICENSE
%doc README

%changelog
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.10-1
- WASM is now Noust: this package only installs noust 3.1.10 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.9-1
- WASM is now Noust: this package only installs noust 3.1.9 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.8-1
- WASM is now Noust: this package only installs noust 3.1.8 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.7-1
- WASM is now Noust: this package only installs noust 3.1.7 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.6-1
- WASM is now Noust: this package only installs noust 3.1.6 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.5-1
- WASM is now Noust: this package only installs noust 3.1.5 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.4-1
- WASM is now Noust: this package only installs noust 3.1.4 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.3-1
- WASM is now Noust: this package only installs noust 3.1.3 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.2-1
- WASM is now Noust: this package only installs noust 3.1.2 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.1-1
- WASM is now Noust: this package only installs noust 3.1.1 and can be removed
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.0-1
- WASM is now Noust: this package only installs noust 3.1.0 and can be removed
* Tue Sep 29 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.0.0-1
- WASM is now Noust: this package only installs noust 3.0.0 and can be removed
* Tue Sep 29 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.3.0-1
- The console in English and Spanish, with notifications in either language
- Recipes: WordPress (PHP-FPM, MariaDB), Uptime Kuma, Umami and n8n, with pinned or checksummed sources
- PHP-FPM applications: a confined pool per application, a fastcgi site that follows the release, a health gate on the pool
- Export and import an application; read Vercel, Railway, Render and Heroku configuration
- The update notice reports the version this server can install from its own package repository
