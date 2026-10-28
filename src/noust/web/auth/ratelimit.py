# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rate limiting, the limit of concurrent connections and the lockout after failed sign-ins.
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from dataclasses import dataclass

from noust.web.auth.constants import (
    LOCKOUT_DURATION,
    MAX_FAILED_ATTEMPTS,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_MAX_TRACKED_IPS,
    RATE_LIMIT_WINDOW,
    WS_MAX_PER_CREDENTIAL,
)


@dataclass
class FailedAttempt:
    """
    Failed authentication attempts recorded for one client.

    Attributes:
        count: Failures inside the current window.
        first_attempt: Monotonic-ish timestamp of the first failure.
        locked_until: Timestamp until which the client is locked out.
    """

    count: int = 0
    first_attempt: float = 0.0
    locked_until: float = 0.0


#: The prefix length an IPv6 client is counted by. A provider gives a customer
#: a /64 or more, 2^64 addresses that all arrive from one place.
IPV6_COUNTED_PREFIX = 64


def limiter_key(identifier: str) -> str:
    """
    Name the client a limiter or a lockout counts a request under.

    An IPv4 address is one client. An IPv6 address is the /64 it belongs to: a
    customer is routinely given a whole /64 and can send every request from a
    different address of it, so counting by exact address gave each request a
    fresh budget and a fresh lockout, and let one network fill the limiter's
    bounded table with entries that pushed everyone else's out. The exact
    address stays where it is the truth: the audit trail, the session's
    binding, an allow-list.

    Anything that is not an address (a credential's name, a ``hooks|`` or
    ``sign-in:`` key) is counted as it is. An IPv4-mapped address is the IPv4
    one, and loopback stays itself.

    Args:
        identifier: What the caller counts by.

    Returns:
        The key: ``identifier`` itself, or ``<prefix>/64`` for an IPv6 address.
    """
    try:
        address = ipaddress.ip_address(identifier)
    except ValueError:
        return identifier
    if not isinstance(address, ipaddress.IPv6Address):
        return identifier
    if address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    if address.is_loopback:
        return identifier
    mask = ((1 << IPV6_COUNTED_PREFIX) - 1) << (128 - IPV6_COUNTED_PREFIX)
    network = ipaddress.IPv6Network((int(address) & mask, IPV6_COUNTED_PREFIX))
    return str(network)


class RateLimiter:
    """
    Sliding window request limiter.

    Keyed by whatever the caller counts by: the server keeps one keyed by
    client IP for anonymous requests and one keyed by credential for
    authenticated ones (see :func:`rate_limit_identity`).
    """

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window: int = RATE_LIMIT_WINDOW,
        max_tracked: int = RATE_LIMIT_MAX_TRACKED_IPS,
    ) -> None:
        """
        Build a rate limiter.

        Args:
            max_requests: Requests allowed per window and client.
            window: Window length in seconds.
            max_tracked: Maximum number of clients kept in memory.
        """
        self.max_requests = max_requests
        self.window = window
        self.max_tracked = max_tracked
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, client_ip: str) -> bool:
        """
        Record a request and report whether it is within the limit.

        Args:
            client_ip: The client's IP address.

        Returns:
            True when the request is allowed, False when rate limited.
        """
        client_ip = limiter_key(client_ip)
        now = time.time()
        with self._lock:
            self._purge(now)
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
            if len(timestamps) >= self.max_requests:
                self._requests[client_ip] = timestamps
                return False
            timestamps.append(now)
            self._requests[client_ip] = timestamps
            self._enforce_capacity()
            return True

    def get_remaining(self, client_ip: str) -> int:
        """
        Report how many requests the client has left in the window.

        Args:
            client_ip: The client's IP address.

        Returns:
            The number of remaining requests.
        """
        client_ip = limiter_key(client_ip)
        now = time.time()
        with self._lock:
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
            return max(0, self.max_requests - len(timestamps))

    def retry_after(self, client_ip: str) -> int:
        """
        Report how long until the client may make its next request.

        Args:
            client_ip: The client's key.

        Returns:
            Whole seconds until the oldest request in the window expires,
            at least 1; 0 when the client is within its budget.
        """
        client_ip = limiter_key(client_ip)
        now = time.time()
        with self._lock:
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
        if len(timestamps) < self.max_requests:
            return 0
        if not timestamps:
            # A budget of zero: nothing ever frees a slot.
            return self.window
        # The request whose expiry brings the count back under the budget.
        freeing = timestamps[len(timestamps) - self.max_requests]
        return max(1, math.ceil(freeing + self.window - now))

    def reset_at(self, client_ip: str) -> int:
        """
        Report when the client's budget is whole again.

        Args:
            client_ip: The client's key.

        Returns:
            The UNIX time, in whole seconds, at which the last request now in the
            window leaves it: the moment the client has its full budget back.
            The present moment when it has made none.
        """
        now = time.time()
        with self._lock:
            timestamps = [ts for ts in self._requests.get(client_ip, []) if now - ts < self.window]
        if not timestamps:
            return math.ceil(now)
        return math.ceil(max(timestamps) + self.window)

    def reset(self, client_ip: str) -> None:
        """
        Forget the history of one client.

        Args:
            client_ip: The client's IP address.
        """
        client_ip = limiter_key(client_ip)
        with self._lock:
            self._requests.pop(client_ip, None)

    def tracked_clients(self) -> int:
        """
        Report how many clients are currently tracked.

        Returns:
            Number of client entries held in memory.
        """
        with self._lock:
            return len(self._requests)

    def _purge(self, now: float) -> None:
        """
        Drop clients with no recent activity.

        Args:
            now: Current timestamp.
        """
        stale = [
            ip
            for ip, timestamps in self._requests.items()
            if not timestamps or now - timestamps[-1] >= self.window
        ]
        for ip in stale:
            del self._requests[ip]

    def _enforce_capacity(self) -> None:
        """Drop the least recently seen clients once the table is full."""
        # Spoofed sources can create entries faster than the window expires them.
        if len(self._requests) <= self.max_tracked:
            return
        oldest = sorted(self._requests, key=lambda ip: self._requests[ip][-1])
        for ip in oldest[: len(self._requests) - self.max_tracked]:
            del self._requests[ip]


class ConnectionLimiter:
    """
    Counts the long-lived connections each credential holds open.

    Keyed by :func:`credential_key`, not by address: the cost being bounded -
    a process and a socket per stream - is spent on behalf of a credential,
    and one script at its limit must not stop the operator's console from
    opening its own.
    """

    def __init__(self, limit: int = WS_MAX_PER_CREDENTIAL) -> None:
        """
        Args:
            limit: Connections allowed per credential at once.
        """
        self.limit = limit
        self._open: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> bool:
        """
        Take a place for one more connection, if there is one.

        Args:
            key: The credential's key.

        Returns:
            True when the connection may open; the caller must
            :meth:`release` it when it closes. False when the credential is
            already at its limit.
        """
        with self._lock:
            held = self._open.get(key, 0)
            if held >= self.limit:
                return False
            self._open[key] = held + 1
            return True

    def release(self, key: str) -> None:
        """
        Give back the place a closed connection held.

        Args:
            key: The credential's key, as passed to :meth:`acquire`.
        """
        with self._lock:
            held = self._open.get(key, 0) - 1
            if held > 0:
                self._open[key] = held
            else:
                self._open.pop(key, None)

    def held(self, key: str) -> int:
        """
        Args:
            key: The credential's key.

        Returns:
            How many connections it holds open now.
        """
        with self._lock:
            return self._open.get(key, 0)


class BruteForceProtection:
    """Lockout of clients that keep failing authentication."""

    def __init__(
        self,
        max_attempts: int = MAX_FAILED_ATTEMPTS,
        lockout_duration: int = LOCKOUT_DURATION,
        max_tracked: int = RATE_LIMIT_MAX_TRACKED_IPS,
    ) -> None:
        """
        Build the lockout tracker.

        Args:
            max_attempts: Failures tolerated before locking a client out.
            lockout_duration: Lockout length in seconds.
            max_tracked: Maximum number of clients kept in memory.
        """
        self.max_attempts = max_attempts
        self.lockout_duration = lockout_duration
        self.max_tracked = max_tracked
        self._failed_attempts: dict[str, FailedAttempt] = {}
        self._lock = threading.Lock()

    def record_failure(self, client_ip: str) -> None:
        """
        Record one failed authentication.

        Args:
            client_ip: The client's IP address.
        """
        client_ip = limiter_key(client_ip)
        now = time.time()
        with self._lock:
            self._purge(now)
            attempt = self._failed_attempts.get(client_ip)
            if attempt is None:
                self._failed_attempts[client_ip] = FailedAttempt(count=1, first_attempt=now)
                return

            if now - attempt.first_attempt > self.lockout_duration:
                attempt.count = 1
                attempt.first_attempt = now
                attempt.locked_until = 0.0
                return

            attempt.count += 1
            if attempt.count >= self.max_attempts:
                attempt.locked_until = now + self.lockout_duration

    def record_success(self, client_ip: str) -> None:
        """
        Clear the failure history of a client after a successful login.

        Args:
            client_ip: The client's IP address.
        """
        client_ip = limiter_key(client_ip)
        with self._lock:
            self._failed_attempts.pop(client_ip, None)

    def is_locked(self, client_ip: str) -> bool:
        """
        Report whether a client is currently locked out.

        Args:
            client_ip: The client's IP address.

        Returns:
            True while the lockout is in force.
        """
        client_ip = limiter_key(client_ip)
        with self._lock:
            attempt = self._failed_attempts.get(client_ip)
            return bool(attempt and attempt.locked_until > time.time())

    def get_lockout_remaining(self, client_ip: str) -> int:
        """
        Report the remaining lockout time.

        Args:
            client_ip: The client's IP address.

        Returns:
            Seconds left, zero when not locked out.
        """
        client_ip = limiter_key(client_ip)
        with self._lock:
            attempt = self._failed_attempts.get(client_ip)
            if not attempt:
                return 0
            return max(0, int(attempt.locked_until - time.time()))

    def get_attempts_remaining(self, client_ip: str) -> int:
        """
        Report how many failures the client has left.

        Args:
            client_ip: The client's IP address.

        Returns:
            Remaining attempts before lockout.
        """
        client_ip = limiter_key(client_ip)
        with self._lock:
            attempt = self._failed_attempts.get(client_ip)
            if not attempt:
                return self.max_attempts
            return max(0, self.max_attempts - attempt.count)

    def _purge(self, now: float) -> None:
        """
        Drop expired lockouts and cap the tracked client count.

        Args:
            now: Current timestamp.
        """
        stale = [
            ip
            for ip, attempt in self._failed_attempts.items()
            if now - attempt.first_attempt > self.lockout_duration and attempt.locked_until <= now
        ]
        for ip in stale:
            del self._failed_attempts[ip]

        if len(self._failed_attempts) > self.max_tracked:
            oldest = sorted(
                self._failed_attempts, key=lambda ip: self._failed_attempts[ip].first_attempt
            )
            for ip in oldest[: len(self._failed_attempts) - self.max_tracked]:
                del self._failed_attempts[ip]
