#!/usr/bin/env python3
"""
setup.py for backwards compatibility with Ubuntu Jammy (22.04).

This file exists because Jammy's older pybuild/setuptools don't fully support
pyproject.toml-only builds. We explicitly provide the essential metadata here.
"""
from setuptools import setup, find_packages

setup(
    name="noust",
    version="3.1.1",
    description="Noust - deploy and manage web applications on Linux servers",
    url="https://github.com/Perkybeet/noust",
    author="Yago López Prado",
    author_email="yago.lopez.adeje@gmail.com",
    license="AGPL-3.0-or-later",
    python_requires=">=3.10",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    include_package_data=True,
    install_requires=[
        "click>=8.0",
        "Jinja2>=3.1.0",
        "PyYAML>=6.0",
        "questionary>=2.0",
        "rich>=13.0",
    ],
    extras_require={
        "web": [
            "fastapi>=0.109.0",
            "starlette>=0.36.0",
            "pydantic>=2.0",
            "uvicorn[standard]>=0.27.0",
                    "psutil>=5.9.0",
            "httpx>=0.25.0",
            "websockets>=10.4",
            "cryptography>=3.4",
        ],
        "monitor": [
            "psutil>=5.9.0",
            "httpx>=0.25.0",
        ],
        "all": [
            "psutil>=5.9.0",
            "httpx>=0.25.0",
            "fastapi>=0.109.0",
            "starlette>=0.36.0",
            "pydantic>=2.0",
            "uvicorn[standard]>=0.27.0",
            "websockets>=10.4",
            "cryptography>=3.4",
                ],
    },
    entry_points={
        "console_scripts": [
            "noust=noust.cli.app:entrypoint",
            # The name WASM had until 3.0, kept for the whole 3.x series.
            "wasm=noust.cli.app:entrypoint",
        ],
    },
    data_files=[
        ("share/man/man1", ["man/noust.1"]),
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ],
)
