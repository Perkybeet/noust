# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Redis key browser: SCAN with a cursor and a pattern, and a bounded preview per type.

**Read-only by the server's rules where it has them.** On Redis 6 and later
(and Valkey) every read signs in as ``wasm_ro_redis``, an ACL user holding
the read-only profile's rules (``-@all +@read +@connection -@dangerous
allkeys``, the same :data:`~noust.managers.database.redis.PROFILE_RULES` an
operator's read-only user gets), so the server refuses a write whatever the
client sends. The browser itself only ever sends the fixed read commands
below, with the key or the pattern as an argument; a server without ACLs
(Redis 5) is read with the client's usual identity and the page says the
guarantee is the browser's, not the server's.

**Bounded.** A page scans at most :data:`MAX_SCAN_COUNT` keys; a preview
reads at most :data:`PREVIEW_ITEMS` elements and :data:`PREVIEW_BYTES` bytes
of a string. Every command of a step travels in one redis-cli process
(:meth:`~noust.managers.database.redis.RedisManager.run_commands`): a page is
the SCAN and one batch of TYPE/TTL/MEMORY USAGE for the keys it found.

Keys are bytes. One that is valid UTF-8 is shown as text; any other is shown
with ``\\xNN`` escapes and carries its exact bytes as ``hex``, which a
preview accepts instead of the text.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ConfigError, DatabaseQueryError, ValidationError
from noust.core.sealing import SealError
from noust.managers.database.instances import storage_name
from noust.managers.database.redis import PROFILE_RULES, RedisManager, RedisSignInError
from noust.managers.database.service import DatabaseService

#: The ACL user the browser reads as. A legacy prefix, like every read-only
#: account Noust keeps, so the service's internal-account guard covers it.
READ_ONLY_USER = "wasm_ro_redis"

#: Where its password is kept in the secret store.
READ_ONLY_SECRET = f"databases/redis/{READ_ONLY_USER}"

#: Keys a page scans, at most and by default. SCAN's COUNT is a hint, so a
#: page may hold a few more or fewer; the cursor says when the scan is done.
MAX_SCAN_COUNT = 1000
DEFAULT_SCAN_COUNT = 100

#: Elements of a list, set, sorted set or hash a preview reads.
PREVIEW_ITEMS = 100

#: Bytes of a string a preview reads, and of any element it shows.
PREVIEW_BYTES = 2000

#: Types a scan may be narrowed to.
KEY_TYPES = ("string", "list", "set", "zset", "hash", "stream")

#: Seconds the read-only user this process set up is reused as it is.
_PROVISION_REUSE_SECONDS = 60.0

_state_lock = threading.Lock()
_provisioned_at: dict[str, float] = {}

#: What one CSV answer is made of.
Token = bytes | int | str | None


class RedisReplyError(DatabaseQueryError):
    """A command of a batch was refused; the message is the server's."""


# ============================================================ CSV answers

_ESCAPES = {"n": 10, "r": 13, "t": 9, "a": 7, "b": 8, "\\": 92, '"': 34}


def parse_csv_reply(line: str) -> list[Token]:
    """
    Read one answer redis-cli printed with ``--csv``.

    Strings are quoted, with ``\\n``, ``\\"``, ``\\\\`` and ``\\xNN`` escapes
    (``sdscatrepr``); integers and ``NULL`` are bare; arrays are flattened
    into one comma-separated line.

    Args:
        line: The line.

    Returns:
        The tokens: bytes for strings, int for integers, None for NULL, the
        bare text for anything else.

    Raises:
        RedisReplyError: When the answer is an error, with the server's words.
    """
    if line.startswith("ERROR,"):
        tokens = _tokens(line[len("ERROR,") :])
        message = tokens[0] if tokens else b""
        text = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
        raise RedisReplyError("Redis refused a command", details=text)
    return _tokens(line)


def _tokens(text: str) -> list[Token]:
    """
    Args:
        text: A CSV answer.

    Returns:
        Its tokens.
    """
    tokens: list[Token] = []
    index, length = 0, len(text)
    while index < length:
        if text[index] == '"':
            index += 1
            buffer = bytearray()
            while index < length and text[index] != '"':
                char = text[index]
                if char == "\\" and index + 1 < length:
                    following = text[index + 1]
                    if following == "x" and index + 3 < length:
                        buffer.append(int(text[index + 2 : index + 4], 16))
                        index += 4
                        continue
                    buffer.append(_ESCAPES.get(following, ord(following)))
                    index += 2
                    continue
                buffer.extend(char.encode("utf-8"))
                index += 1
            index += 1
            tokens.append(bytes(buffer))
        else:
            end = text.find(",", index)
            end = length if end < 0 else end
            bare = text[index:end]
            index = end
            if bare == "NULL":
                tokens.append(None)
            elif bare.lstrip("-").isdigit():
                tokens.append(int(bare))
            elif bare:
                tokens.append(bare)
        if index < length and text[index] == ",":
            index += 1
    return tokens


def _answers(output: str, expected: int) -> list[str]:
    """
    Split a batch's output into one line per command.

    Args:
        output: redis-cli's output.
        expected: How many commands were sent.

    Returns:
        The lines.

    Raises:
        DatabaseQueryError: When the count does not match, with the output.
    """
    lines = output.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != expected:
        raise DatabaseQueryError(
            "redis-cli answered in an unexpected shape",
            details=f"Noust sent {expected} commands and read {len(lines)} answers.",
            output=output.strip()[:2000],
        )
    return lines


# ============================================================ values


def show_bytes(data: bytes) -> str:
    """
    Render bytes as text: UTF-8 when it is, ``\\xNN`` escapes where not.

    Args:
        data: The bytes.

    Returns:
        The text.
    """
    return data.decode("utf-8", "backslashreplace")


def _value(data: Token, limit: int = PREVIEW_BYTES) -> Any:
    """
    Render one stored value for a preview.

    Args:
        data: The value's token.
        limit: Bytes shown of it.

    Returns:
        The text when it is UTF-8, else ``{"bytes": n, "hex": prefix}``.
    """
    if not isinstance(data, bytes):
        return data
    try:
        return data[:limit].decode("utf-8")
    except UnicodeDecodeError:
        return {"bytes": len(data), "hex": data[:32].hex()}


@dataclass
class KeyInfo:
    """
    One key, as a page lists it.

    Attributes:
        key: The key, as text (``\\xNN`` where it is not UTF-8).
        hex: Its exact bytes, when it is not UTF-8.
        type: ``string``, ``list``, ``set``, ``zset``, ``hash``, ``stream``,
            or the module type the server names.
        ttl: Seconds until it expires; None when it never does.
        memory: Bytes it takes (``MEMORY USAGE``), when the server says.
    """

    key: str
    hex: str | None = None
    type: str = "none"
    ttl: int | None = None
    memory: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The key as plain data.
        """
        return {
            "key": self.key,
            "hex": self.hex,
            "type": self.type,
            "ttl": self.ttl,
            "memory": self.memory,
        }


@dataclass
class KeysPage:
    """
    One page of a scan.

    Attributes:
        keys: The keys found.
        cursor: Where the next page starts; ``"0"`` when the scan is done.
        read_only_enforced: Whether the server itself held the reads to
            read-only (an ACL user); False on a server without ACLs.
    """

    keys: list[KeyInfo] = field(default_factory=list)
    cursor: str = "0"
    read_only_enforced: bool = True

    @property
    def done(self) -> bool:
        """Whether the scan is complete."""
        return self.cursor == "0"


@dataclass
class KeyValue:
    """
    A bounded preview of one key.

    Attributes:
        key: The key, as text.
        hex: Its exact bytes, when it is not UTF-8.
        type: Its type.
        ttl: Seconds until it expires; None when it never does.
        memory: Bytes it takes, when the server says.
        length: Its length: bytes of a string, elements of anything else.
        value: A string's text (or ``{"bytes", "hex"}``); a list's or a
            set's elements; a hash's ``[field, value]`` pairs; a sorted set's
            ``[member, score]`` pairs; None for a type not previewed.
        truncated: Whether the value shown is only part of it.
        read_only_enforced: As in :class:`KeysPage`.
    """

    key: str
    hex: str | None
    type: str
    ttl: int | None
    memory: int | None
    length: int | None
    value: Any
    truncated: bool
    read_only_enforced: bool = True

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The preview as plain data.
        """
        return {
            "key": self.key,
            "hex": self.hex,
            "type": self.type,
            "ttl": self.ttl,
            "memory": self.memory,
            "length": self.length,
            "value": self.value,
            "truncated": self.truncated,
            "read_only_enforced": self.read_only_enforced,
        }


# ============================================================ browser


class KeyBrowser:
    """
    Scan a Redis slot's keys and preview one.

    Construct one per request or command over the
    :class:`~noust.managers.database.service.DatabaseService`.
    """

    def __init__(self, service: DatabaseService) -> None:
        """
        Args:
            service: The database service: engines, the actor, the audit trail.
        """
        self.service = service

    def _open(self, engine: str, slot: str) -> tuple[RedisManager, int]:
        """
        Resolve a Redis engine and a slot number.

        Args:
            engine: The engine, as the caller named it.
            slot: The slot, as text.

        Returns:
            The manager and the slot.

        Raises:
            DatabaseQueryError: When the engine has no keys to browse.
            ValidationError: When the slot is not a number from 0 to 255.
        """
        manager = self.service.manager(engine)
        if not isinstance(manager, RedisManager) or "keys" not in manager.CAPABILITIES:
            raise DatabaseQueryError(
                f"{manager.DISPLAY_NAME} has no keys to browse",
                details="The key browser works on Redis and Valkey; SQL engines have the data explorer.",
            )
        if not slot.isdigit() or int(slot) > 255:
            raise ValidationError(
                f"Invalid Redis database number: {slot!r}",
                details="Redis databases are numbered from 0; pass a number such as 0.",
            )
        return manager, int(slot)

    # ------------------------------------------------------------ identity

    def _read_only_account(
        self, manager: RedisManager, *, force: bool = False
    ) -> tuple[str, str] | None:
        """
        Set up, or reuse, the ACL user the browser reads as.

        Args:
            manager: The engine's manager.
            force: Set it up again even when it was set up moments ago.

        Returns:
            The user and its password, or None on a server without ACLs.

        Raises:
            DatabaseQueryError: When the server refuses for another reason.
        """
        secret_store = self.service.secrets
        # A Redis in a container has its own ACL users: its read-only
        # password is kept apart from the host's.
        secret = (
            READ_ONLY_SECRET
            if manager.instance is None
            else f"databases/{storage_name(manager.ENGINE_NAME)}/{READ_ONLY_USER}"
        )
        try:
            password = secret_store.read(secret)
        except (ConfigError, SealError) as exc:
            self.service.logger.warning(f"Could not read the key browser's password: {exc}")
            password = None
        with _state_lock:
            last = _provisioned_at.get(manager.ENGINE_NAME, -1e9)
            fresh = time.monotonic() - last <= _PROVISION_REUSE_SECONDS
        if password and fresh and not force:
            return READ_ONLY_USER, password
        if not password:
            password = secrets.token_urlsafe(32)
        output = manager.run_commands(
            [
                [
                    "ACL",
                    "SETUSER",
                    READ_ONLY_USER,
                    "reset",
                    "on",
                    f">{password}",
                    *PROFILE_RULES["read_only"],
                ]
            ],
            secrets=(password,),
        )
        try:
            parse_csv_reply(output.strip())
        except RedisReplyError as exc:
            if "unknown command" in exc.details.lower():
                return None
            raise
        secret_store.write(secret, password)
        with _state_lock:
            _provisioned_at[manager.ENGINE_NAME] = time.monotonic()
        return READ_ONLY_USER, password

    def _batch(
        self, manager: RedisManager, slot: int, commands: Sequence[Sequence[str | bytes]]
    ) -> tuple[list[str], bool]:
        """
        Run read commands in one process, as the read-only user when there is one.

        A sign-in the server refuses (the user was dropped, a restart forgot
        it) sets the user up again and retries once. redis-cli carries on
        after a refused AUTH with whatever identity is left, so a refusal is
        never ignored.

        Args:
            manager: The engine's manager.
            slot: The slot.
            commands: The commands.

        Returns:
            One answer line per command, and whether the server enforced
            read-only.

        Raises:
            DatabaseQueryError: When the reads cannot be made.
        """
        account = self._read_only_account(manager)
        for attempt in range(2):
            if account is None:
                output = manager.run_commands(commands, db=slot)
                return _answers(output, len(commands)), False
            user, password = account
            result = self._run_as(manager, slot, commands, user, password)
            if result is not None:
                return _answers(result, len(commands)), True
            if attempt == 0:
                account = self._read_only_account(manager, force=True)
        raise DatabaseQueryError(
            f"The key browser could not sign in to Redis as {READ_ONLY_USER}",
            details=(
                "Redis refused the password Noust set a moment ago. Check that the ACL user "
                f"{READ_ONLY_USER} is not managed by an aclfile Noust cannot write."
            ),
        )

    @staticmethod
    def _run_as(
        manager: RedisManager,
        slot: int,
        commands: Sequence[Sequence[str | bytes]],
        user: str,
        password: str,
    ) -> str | None:
        """
        Args:
            manager: The engine's manager.
            slot: The slot.
            commands: The commands.
            user: The ACL user.
            password: Its password.

        Returns:
            The output, or None when the sign-in was refused.
        """
        try:
            output = manager.run_commands(
                commands, db=slot, username=user, password=password, secrets=(password,)
            )
        except RedisSignInError:
            return None
        first = output.split("\n", 1)[0]
        if "WRONGPASS" in first or "NOAUTH" in first:
            return None
        return output

    # ------------------------------------------------------------ reads

    def scan(
        self,
        engine: str,
        slot: str,
        *,
        match: str | None = None,
        cursor: str = "0",
        count: int = DEFAULT_SCAN_COUNT,
        key_type: str | None = None,
    ) -> KeysPage:
        """
        Read one page of a slot's keys.

        Args:
            engine: The engine.
            slot: The database slot.
            match: A glob pattern (``user:*``).
            cursor: Where to continue; ``"0"`` to start.
            count: Keys to scan, at most :data:`MAX_SCAN_COUNT`.
            key_type: Only keys of this type (Redis 6 and later).

        Returns:
            The page: each key with its type, TTL and memory.

        Raises:
            ValidationError: When the cursor, the count or the type is not
                acceptable.
        """
        manager, number = self._open(engine, slot)
        if not cursor.isdigit():
            raise ValidationError("Invalid scan cursor", details="Start again from cursor 0.")
        if count < 1 or count > MAX_SCAN_COUNT:
            raise ValidationError(
                f"A scan reads from 1 to {MAX_SCAN_COUNT} keys", details="Ask for fewer keys."
            )
        if key_type is not None and key_type not in KEY_TYPES:
            raise ValidationError(
                f"Unknown key type: {key_type!r}", details=f"Use one of: {', '.join(KEY_TYPES)}."
            )
        command: list[str | bytes] = ["SCAN", cursor]
        if match:
            if "\x00" in match:
                raise ValidationError("The pattern contains a NUL byte", details="Remove it.")
            command += ["MATCH", match]
        command += ["COUNT", str(count)]
        if key_type:
            command += ["TYPE", key_type]
        (line,), enforced = self._batch(manager, number, [command])
        tokens = parse_csv_reply(line)
        next_cursor = tokens[0].decode() if tokens and isinstance(tokens[0], bytes) else "0"
        keys = [token for token in tokens[1:] if isinstance(token, bytes)]
        page = KeysPage(cursor=next_cursor, read_only_enforced=enforced)
        if not keys:
            return page
        batch: list[list[str | bytes]] = []
        for key in keys:
            batch += [["TYPE", key], ["TTL", key], ["MEMORY", "USAGE", key]]
        answers, _ = self._batch(manager, number, batch)
        for index, key in enumerate(keys):
            kind, ttl, memory = answers[index * 3 : index * 3 + 3]
            page.keys.append(
                KeyInfo(
                    key=show_bytes(key),
                    hex=None if _is_utf8(key) else key.hex(),
                    type=_first_text(kind) or "none",
                    ttl=_ttl(ttl),
                    memory=_first_int(memory),
                )
            )
        return page

    def preview(
        self, engine: str, slot: str, key: str | None = None, *, key_hex: str | None = None
    ) -> KeyValue:
        """
        Read a bounded preview of one key.

        Args:
            engine: The engine.
            slot: The database slot.
            key: The key, as text.
            key_hex: The key's exact bytes, for a key that is not UTF-8.

        Returns:
            The preview.

        Raises:
            ValidationError: When neither or both of ``key`` and ``key_hex``
                are given, or the hex is not hex.
            DatabaseNotFoundError: When the key does not exist.
        """
        from noust.core.exceptions import DatabaseNotFoundError

        manager, number = self._open(engine, slot)
        if (key is None) == (key_hex is None):
            raise ValidationError(
                "Name one key", details="Send the key, or its hex for a binary key."
            )
        try:
            raw = bytes.fromhex(key_hex) if key_hex is not None else (key or "").encode()
        except ValueError as exc:
            raise ValidationError(
                "Invalid key hex", details="Send an even number of hex digits."
            ) from exc

        head, enforced = self._batch(
            manager, number, [["TYPE", raw], ["TTL", raw], ["MEMORY", "USAGE", raw]]
        )
        kind = _first_text(head[0]) or "none"
        if kind == "none":
            raise DatabaseNotFoundError(
                f"Key {show_bytes(raw)!r} does not exist in database {number}",
                details="It expired or was deleted since the page was read.",
            )
        commands = _preview_commands(kind, raw)
        answers, _ = self._batch(manager, number, commands) if commands else ([], enforced)
        length, value, truncated = _preview_value(kind, answers)
        self.service.audit(
            "db.browse", f"{manager.ENGINE_NAME}/{number}", key=show_bytes(raw)[:200], type=kind
        )
        return KeyValue(
            key=show_bytes(raw),
            hex=None if _is_utf8(raw) else raw.hex(),
            type=kind,
            ttl=_ttl(head[1]),
            memory=_first_int(head[2]),
            length=length,
            value=value,
            truncated=truncated,
            read_only_enforced=enforced,
        )


def _preview_commands(kind: str, key: bytes) -> list[list[str | bytes]]:
    """
    Args:
        kind: The key's type.
        key: The key.

    Returns:
        The length command and the bounded read for that type.
    """
    last = str(PREVIEW_ITEMS - 1)
    count = str(PREVIEW_ITEMS)
    commands: dict[str, list[list[str | bytes]]] = {
        "string": [["STRLEN", key], ["GETRANGE", key, "0", str(PREVIEW_BYTES - 1)]],
        "list": [["LLEN", key], ["LRANGE", key, "0", last]],
        "set": [["SCARD", key], ["SSCAN", key, "0", "COUNT", count]],
        "zset": [["ZCARD", key], ["ZRANGE", key, "0", last, "WITHSCORES"]],
        "hash": [["HLEN", key], ["HSCAN", key, "0", "COUNT", count]],
        "stream": [["XLEN", key]],
    }
    return commands.get(kind, [])


def _preview_value(kind: str, answers: list[str]) -> tuple[int | None, Any, bool]:
    """
    Args:
        kind: The key's type.
        answers: The answers to :func:`_preview_commands`.

    Returns:
        Its length, the value shown and whether it is only part of it.
    """
    if not answers:
        return None, None, False
    length = _first_int(answers[0])
    if kind == "stream" or len(answers) < 2:
        return length, None, bool(length)
    tokens = parse_csv_reply(answers[1])
    if kind == "string":
        data = tokens[0] if tokens and isinstance(tokens[0], bytes) else b""
        return length, _value(data), (length or 0) > len(data)
    if kind in ("set", "hash"):
        tokens = tokens[1:]  # the scan's cursor
    if kind in ("hash", "zset"):
        pairs = [[_value(tokens[i]), _value(tokens[i + 1])] for i in range(0, len(tokens) - 1, 2)][
            :PREVIEW_ITEMS
        ]
        return length, pairs, (length or 0) > len(pairs)
    items = [_value(token) for token in tokens][:PREVIEW_ITEMS]
    return length, items, (length or 0) > len(items)


def _is_utf8(data: bytes) -> bool:
    """
    Args:
        data: Bytes.

    Returns:
        Whether they are valid UTF-8.
    """
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _first_text(line: str) -> str | None:
    """
    Args:
        line: One answer.

    Returns:
        Its first token as text, or None (an error or an empty answer).
    """
    try:
        tokens = parse_csv_reply(line)
    except RedisReplyError:
        return None
    if not tokens or tokens[0] is None:
        return None
    first = tokens[0]
    return first.decode("utf-8", "replace") if isinstance(first, bytes) else str(first)


def _first_int(line: str) -> int | None:
    """
    Args:
        line: One answer.

    Returns:
        Its first token as an integer, or None.
    """
    try:
        tokens = parse_csv_reply(line)
    except RedisReplyError:
        return None
    return tokens[0] if tokens and isinstance(tokens[0], int) else None


def _ttl(line: str) -> int | None:
    """
    Args:
        line: The answer to TTL.

    Returns:
        Seconds left, or None for a key that never expires.
    """
    value = _first_int(line)
    return value if value is not None and value >= 0 else None
