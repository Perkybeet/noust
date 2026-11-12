# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the shapes of a secret hide in a piece of text, and what they leave alone: the one table.

Two places mask text by what it looks like: Python, in
:func:`noust.core.secret_detection.mask_secret_shapes`, and the console, in the block of the
browser it adds to a report of a problem (``panel/src/features/report/redact.ts``). They read the
same shapes (``scripts/generate_secret_shapes.py`` writes them into
``panel/src/api/secretShapes.gen.ts``), and they are held to the same cases: Python runs this table
through ``mask_secret_shapes``, the generator writes it into the same file, and the console's
tests run it through its own masking. A shape one side reads differently from the other breaks a
test on one side or the other.

The placeholder is the literal ``***`` (:data:`noust.core.config.REDACTED`) because the console
writes that one. Every case is ASCII: the two languages read ``\\b`` and ``\\s`` alike there.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What the two sides write where a secret was.
MASK = "***"

_PEM_BODY = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW"


@dataclass(frozen=True)
class ShapeCase:
    """
    One line of text and what masking it must leave.

    Attributes:
        name: What the case is about, for a failing test's message.
        text: The text as it comes.
        masked: What the text is after masking; the same text when nothing in it is a secret.
    """

    name: str
    text: str
    masked: str


def _hides(name: str, text: str, masked: str) -> ShapeCase:
    return ShapeCase(name=name, text=text, masked=masked)


def _keeps(name: str, text: str) -> ShapeCase:
    return ShapeCase(name=name, text=text, masked=text)


def _token(name: str, token: str) -> ShapeCase:
    return _hides(name, f"deploy used {token} to push", f"deploy used {MASK} to push")


CASES: tuple[ShapeCase, ...] = (
    # A vendor's token is known by its prefix, wherever it appears.
    _token("github token", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    _token(
        "github fine-grained token",
        "github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789ABCDEFGHIJ",
    ),
    _token("gitlab token", "glpat-abcdefghij0123456789"),
    _token("slack token", "xoxb-1234567890-abcdefghijkl"),
    _token("aws access key", "AKIAIOSFODNN7EXAMPLE"),
    _token("stripe key", "sk_live_4eC39HqLyjWDarjtT1zdp7dc"),
    _token("stripe webhook secret", "whsec_abcdefghijklmnopqrstuvwxyz"),
    _token("google api key", "AIzaSyA-1234567890abcdefghijklmnopqrstu"),
    _token("sendgrid key", "SG.abcdefghijklmnop.qrstuvwxyz0123456789"),
    _token("npm token", "npm_" + "a1B2c3D4" * 4 + "e5F6"),
    _token("telegram bot token", "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"),
    _token("noust token", "noust_tok_abcdefghijklmnop0123456789"),
    _token("token of before 3.0", "wasm_tok_abcdefghijklmnop0123456789"),
    _token("provider api key", "sk-" + "abcdefghij" * 4),
    _token(
        "json web token",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    ),
    _hides(
        "a token at the start of the line",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        MASK,
    ),
    _hides(
        "a token given to curl",
        "curl -u glpat-abcdefghij0123456789 https://x.example.test",
        f"curl -u {MASK} https://x.example.test",
    ),
    # A credential named by what stands before it keeps its name.
    _hides(
        "an authorization header keeps its scheme",
        "Authorization: Bearer abcdefghijklmnop12345",
        f"Authorization: Bearer {MASK}",
    ),
    _keeps(
        "an authorization header already masked keeps its scheme", f"Authorization: Bearer {MASK}"
    ),
    _hides(
        "a basic authorization header",
        "authorization: basic dXNlcjpwYXNzd29yZA==",
        f"authorization: basic {MASK}",
    ),
    _hides(
        "a bearer token in a curl line",
        "curl -H 'Authorization: Bearer abcdefghijklmnop12345' x",
        f"curl -H 'Authorization: Bearer {MASK}' x",
    ),
    _hides(
        "a bare bearer credential",
        "sent Bearer abcdefghijklmnopqrstuvwxyz to the api",
        f"sent Bearer {MASK} to the api",
    ),
    _hides("an assigned password", "DB_PASSWORD=hunter2222", f"DB_PASSWORD={MASK}"),
    _hides("a quoted password", "password: 'hunter2222'", f"password: '{MASK}'"),
    _hides(
        "a password in json",
        '{"password": "hunter2222", "user": "bob"}',
        f'{{"password": "{MASK}", "user": "bob"}}',
    ),
    _hides("an api key header", "X-Api-Key: 0123456789abcdef", f"X-Api-Key: {MASK}"),
    _hides("a client secret", "client_secret=abcdefghijklmnop", f"client_secret={MASK}"),
    _hides(
        "a secret key with slashes",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        f"AWS_SECRET_ACCESS_KEY={MASK}",
    ),
    _hides(
        "an access token in a query keeps the rest",
        "access_token=abcdef123456&state=ok",
        f"access_token={MASK}&state=ok",
    ),
    _hides(
        "a token in the address of the page",
        "/apps?token=0123456789abcdef&tab=logs",
        f"/apps?token={MASK}&tab=logs",
    ),
    # A password inside an address.
    _hides(
        "a password in a connection string",
        "connecting to postgres://app:hunter22@db.internal:5432/shop",
        f"connecting to postgres://app:{MASK}@db.internal:5432/shop",
    ),
    _hides(
        "a redis url with only a password",
        "redis://:s3cr3tvalue@cache:6379/0",
        f"redis://:{MASK}@cache:6379/0",
    ),
    # Where a scheme may start: at the beginning of the text and after any character that is not
    # one of a scheme's own, which is what keeps a long run of letters from being read from every
    # letter. Whatever stands before the scheme stays as it was.
    _hides(
        "a connection string that is the whole text",
        "postgres://app:hunter22@db.internal/shop",
        f"postgres://app:{MASK}@db.internal/shop",
    ),
    _hides(
        "a connection string in brackets",
        "(postgres://app:hunter22@db.internal)",
        f"(postgres://app:{MASK}@db.internal)",
    ),
    _hides(
        "a connection string in quotes after an equals sign",
        'DATABASE_URL="mysql://root:hunter22@db:3306/shop"',
        f'DATABASE_URL="mysql://root:{MASK}@db:3306/shop"',
    ),
    _hides(
        "a connection string after an underscore",
        "_postgres://app:hunter22@db.internal/shop",
        f"_postgres://app:{MASK}@db.internal/shop",
    ),
    _hides(
        "two connection strings one after the other",
        "primary postgres://app:one11111@a replica postgres://app:two22222@b",
        f"primary postgres://app:{MASK}@a replica postgres://app:{MASK}@b",
    ),
    _hides(
        "a connection string right after the at sign of another",
        "a://u:one11111@b://v:two22222@h",
        f"a://u:{MASK}@b://v:{MASK}@h",
    ),
    _hides(
        "a scheme with the characters a scheme may have",
        "git+ssh://deploy:hunter22@git.example.test/app.git",
        f"git+ssh://deploy:{MASK}@git.example.test/app.git",
    ),
    # A private key goes whole, or to the end when the text was cut before its footer.
    _hides(
        "a private key block",
        f"before\n-----BEGIN OPENSSH PRIVATE KEY-----\n{_PEM_BODY}\n"
        "-----END OPENSSH PRIVATE KEY-----\nafter",
        f"before\n{MASK}\nafter",
    ),
    _hides(
        "a private key cut off before its footer",
        "key follows\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890",
        f"key follows\n{MASK}",
    ),
    _hides("a private key header alone", "-----BEGIN RSA PRIVATE KEY-----", MASK),
    # What is random-looking or secret-sounding and is not one stays as it is.
    _keeps("a git commit", "commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 on main"),
    _keeps("a uuid", "request id 550e8400-e29b-41d4-a716-446655440000 took 12ms"),
    _keeps("a path", "serving /var/www/apps/shop-example-com/current/public"),
    _keeps("a route of the console", "/databases/shop_prod/data?tab=rows"),
    _keeps("a url with no credential", "listening on http://localhost:3000"),
    _keeps("a url with a port", "GET http://db.internal:5432/shop 200"),
    _keeps(
        "a name that only contains a sensitive word",
        "the tokenizer=spacy_model_big loaded; bypass=allowed-for-all",
    ),
    _keeps("a short value", "password: true"),
    _keeps("a number", "token_expiration_hours=12"),
    _keeps("a request to the tokens endpoint", "GET /api/auth/tokens?limit=10 200"),
    _keeps("a public key", "-----BEGIN PUBLIC KEY-----"),
    _keeps("a certificate", "-----BEGIN CERTIFICATE-----"),
    _keeps("a javascript error", "TypeError: Cannot read properties of undefined (reading 'rows')"),
    _keeps("nothing", ""),
)
