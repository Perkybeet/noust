"""Builds the archives the uploaded-source scenario sends, and the hostile ones it expects refused.

Run as ``python3 -I make_archives.py <node fixture> <output directory>``. Python and not tar,
because the shell's tar will not write a member named ``../x`` or a setuid one on purpose.
"""

import io
import os
import sys
import tarfile
import zipfile

source, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)


def project(version):
    """The Node fixture's files at a VERSION."""
    files = {}
    for name in ("server.js", "package.json", "package-lock.json"):
        with open(os.path.join(source, name), "rb") as handle:
            files[name] = handle.read()
    files["VERSION"] = (version + "\n").encode()
    return files


# 1: a tar.gz with the project at its root.
with tarfile.open(os.path.join(out, "app-1.tar.gz"), "w:gz") as archive:
    for name, data in project("1").items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

# 2: a zip with one directory wrapping everything, as a code host's "download ZIP" makes.
with zipfile.ZipFile(os.path.join(out, "app-2.zip"), "w") as archive:
    for name, data in project("2").items():
        archive.writestr("app-main/" + name, data)


def hostile(name, build):
    """Write a tar.gz whose only member is the one ``build`` adds."""
    with tarfile.open(os.path.join(out, name), "w:gz") as archive:
        build(archive)


def member(archive, name, data=b"x", mode=0o644, kind=tarfile.REGTYPE, link=""):
    """Add one member of any kind."""
    info = tarfile.TarInfo(name)
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    info.mode = mode
    info.type = kind
    info.linkname = link
    archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


hostile("hostile-dotdot.tar.gz", lambda a: member(a, "../outside.txt"))
hostile("hostile-absolute.tar.gz", lambda a: member(a, "/tmp/it-absolute.txt"))
hostile("hostile-link.tar.gz", lambda a: member(a, "etc", kind=tarfile.SYMTYPE, link="/etc"))
hostile("hostile-setuid.tar.gz", lambda a: member(a, "su-me", mode=0o4755))
hostile("hostile-device.tar.gz", lambda a: member(a, "null", kind=tarfile.CHRTYPE))
with open(os.path.join(out, "not-an-archive.tar.gz"), "wb") as handle:
    handle.write(b"this is not an archive\n")
