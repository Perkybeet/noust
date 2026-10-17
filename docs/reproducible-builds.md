# Reproducible builds: the package is the tag

A tag names a commit. The wheel and the source distribution of a release are built from that
commit, and anyone who builds that commit with the same tools gets the same bytes, so the file you
download can be checked against the source rather than against the word of whoever uploaded it. The
signature of a release says *who* built it; this says *what from*.

```bash
git clone https://github.com/Perkybeet/noust && cd noust
pip install "build==1.6.1" "setuptools==84.0.0" "wheel==0.48.0"      # the tools the release names
python scripts/reproducible_build.py verify --ref vVERSION --against ./downloaded-release-files
```

`verify` builds the commit **twice, in two different directories**, and compares every file of the
two builds with each other and, with `--against`, with the files you downloaded (the wheel and the
`.tar.gz` of the GitHub release, or of PyPI). It prints the SHA-256 of each and exits 0 when they
agree, 1 when one differs, 2 when it could not build. `build` writes the two files (`--output DIR`,
`--json` for the record: commit, time, tools, hashes).

The pinned tools above are the ones the release workflow and CI use (`.github/workflows/ci.yml`, job
`reproducible`, runs `verify` on every change). A different setuptools writes a different wheel, so
the versions are printed with the result; `--isolated` builds with whatever setuptools is newest
instead, which is convenient and not reproducible across days.

## What the script fixes

Three things made two builds of one commit differ:

| Why they differed | What is done |
|---|---|
| **Time.** A wheel is a zip whose entries carry a modification time. | `SOURCE_DATE_EPOCH` is the commit's own time (`git log -1 --format=%ct`); setuptools and wheel honour it. |
| **The source distribution.** Its tar entries carry the time of the build, the user who built it and the order of the filesystem, and its gzip header the time it was compressed. | It is repacked: entries sorted, owned by nobody, with a mode that depends only on whether the file is executable, never newer than the commit, and a gzip header with no name and no time. |
| **The tree.** Whatever lies in the working directory (`build/`, an ignored file, an editor's backup) could get in. | What is built is `git archive` of the commit, never the working directory. It is also how the tarball for the Debian and RPM builds is made. |

The release workflow builds the files it attaches to the GitHub release (`build-sdist`) and the files
it uploads to PyPI (`publish-pypi`) in two separate jobs, with this script and the same pinned tools:
they are the same bytes.

## What is not covered

* **The Debian and RPM packages** are built by OBS from the same `git archive` tarball, with OBS's own
  toolchain; they are signed by the repository key but are not bit-for-bit reproducible yet.
* **The container image** is signed and attested by digest; building the same image twice is not
  verified.
* **The console** is a committed build (`src/noust/web/static`), not rebuilt when the package is
  built. CI's `panel` job rebuilds it from `panel/` and fails when the committed files differ, so the
  files in a tag are the files its sources produce, with the Node and npm versions of
  `panel/.nvmrc` and `panel/package-lock.json`.
* **The tools.** Reproducibility holds for the tool versions named above. A verifier that uses others
  gets a different wheel and cannot tell why from the hash alone: compare the versions printed first.
