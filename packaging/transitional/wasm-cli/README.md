# wasm-cli is now noust

WASM was renamed to **Noust** in 3.0.0. This package contains no code: it
depends on [noust](https://pypi.org/project/noust/) at the same version, so an
existing `pip install -U wasm-cli` keeps working and installs Noust.

Install Noust directly from now on:

```bash
pip install noust
```

The command is `noust`. `wasm` remains an alias for the whole 3.x series, so
scripts and cron lines that call it keep working.

If you installed with pipx, replace the old application:

```bash
pipx uninstall wasm-cli
pipx install noust
```

Source, documentation and releases: https://github.com/Perkybeet/noust
