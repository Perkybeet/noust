# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking an application whether it came up, and reading what failed when it did not.
"""

from __future__ import annotations

import ssl
import time
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import IO
from urllib.error import HTTPError, URLError

from noust.core.runner import CommandResult

#: A local HTTP request that has not answered in this long is not going to.
PROBE_TIMEOUT = 5

#: A command that has not said whether the application is up in this long is
#: not going to: longer than a request, because a program starts a runtime.
COMMAND_TIMEOUT = 30


def _loopback_tls() -> ssl.SSLContext:
    """
    Build the TLS context of a probe of the application's own port.

    The probe always connects to ``127.0.0.1``, and an address is not what a
    certificate names, so verifying it would fail every application that serves
    TLS itself, with a certificate of its own or none worth trusting. Nothing
    leaves the machine: the point of the probe is whether the process answers.

    Returns:
        A client context that does not verify the certificate.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _probe_opener(url: str, *, follow_redirects: bool) -> urllib.request.OpenerDirector:
    """
    Build the opener that requests one probe.

    Args:
        url: The address probed, always ``<scheme>://127.0.0.1:<port><path>``.
        follow_redirects: Whether a redirect is followed. A check with an
            expectation takes the redirect itself as the answer.

    Returns:
        An opener that speaks TLS without verifying it when the scheme is ``https``.
    """
    handlers: list[urllib.request.BaseHandler] = []
    if not follow_redirects:
        handlers.append(_NoRedirect())
    if url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=_loopback_tls()))
    return urllib.request.build_opener(*handlers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect as the answer instead of following it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        """
        Decline every redirect, so it surfaces as an HTTPError with its code.

        Args:
            req: The request that was redirected.
            fp: The response body.
            code: The redirect status.
            msg: The reason phrase.
            headers: The response headers.
            newurl: Where the redirect points.

        Returns:
            None, which makes urllib raise instead of following.
        """
        return None


def answers(status: int) -> bool:
    """
    Tell whether a status code means the application is up.

    Anything but a server error: a 404 on ``/``, a login page's 401 or a
    redirect to HTTPS all come from a process that started and is routing
    requests. Rolling back a release because its root path is not a page
    would throw away a good build.

    Args:
        status: HTTP status code of the response.

    Returns:
        True for every status below 500.
    """
    return status < 500


def _poll(
    attempt: Callable[[float], bool],
    *,
    retries: int,
    delay: float,
    within: float | None,
    clock: Callable[[], float] | None,
    sleep: Callable[[float], None] | None,
    attempt_timeout: float = PROBE_TIMEOUT,
) -> bool:
    """
    Call a probe until it succeeds, the attempts run out or time is up.

    The one loop both kinds of check wait with, so "how long may a release
    take" has one answer whether the check is a request or a command.

    Args:
        attempt: Makes one probe, given the seconds it may take, and returns
            whether the application is up. It reports its own failures.
        retries: Most attempts to make.
        delay: Seconds to wait between attempts.
        within: Seconds of wall-clock time the whole poll may take, measured
            on a monotonic clock. No attempt waits longer than what is left,
            and no pause runs past it. None bounds the poll by ``retries``
            alone, each attempt allowed :data:`PROBE_TIMEOUT`.
        clock: Monotonic time source; :func:`time.monotonic` when None.
        sleep: How to pause; :func:`time.sleep` when None.
        attempt_timeout: The most one attempt is allowed.

    Returns:
        True when an attempt succeeded.
    """
    now = clock or time.monotonic
    pause = sleep or time.sleep
    deadline = now() + within if within is not None else None
    for number in range(retries):
        probe_timeout: float = attempt_timeout
        if deadline is not None:
            left = deadline - now()
            if left <= 0:
                break
            probe_timeout = min(attempt_timeout, left)
        if attempt(probe_timeout):
            return True
        if number == retries - 1:
            break
        if deadline is None:
            pause(delay)
            continue
        left = deadline - now()
        if left <= 0:
            break
        pause(min(delay, left))
    return False


def wait_until_healthy(
    url: str,
    *,
    retries: int = 5,
    delay: float = 2.0,
    on_attempt: Callable[[str], None] | None = None,
    accept: Callable[[int], bool] | None = None,
    within: float | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    method: str = "GET",
    host: str | None = None,
) -> bool:
    """
    Poll an endpoint until it answers as required, the attempts run out or time is up.

    Args:
        url: Endpoint to request.
        retries: Most attempts to make.
        delay: Seconds to wait between attempts.
        on_attempt: Called with a description of each failed attempt.
        accept: Decides which status codes count as healthy, such as
            :func:`answers`. When given, redirects are not followed: the
            redirect itself is the answer, and following one to the public
            HTTPS name would probe something other than this process. None
            keeps the strict "200 after redirects" check.
        within: Seconds of wall-clock time the whole poll may take, measured
            on a monotonic clock. No attempt waits longer than what is left,
            and no pause runs past it. None bounds the poll by ``retries``
            alone, each attempt allowed :data:`PROBE_TIMEOUT`.
        clock: Monotonic time source; :func:`time.monotonic` when None.
        sleep: How to pause; :func:`time.sleep` when None.
        method: The request method.
        host: The ``Host:`` header to present, for an application that
            answers one name and refuses the address it is probed on. The
            connection is still to the URL's own address.

    Returns:
        True when the endpoint answered as required.
    """
    opener = _probe_opener(url, follow_redirects=accept is None)
    # A plain URL is what every probe has always been; a Request only where
    # the check asks for another method or a host name.
    target: str | urllib.request.Request = url
    if method != "GET" or host is not None:
        # The URL is always <scheme>://127.0.0.1:<port><path>, built by the
        # health check from validated parts; S310 guards against a
        # caller-supplied scheme, which cannot occur.
        target = urllib.request.Request(
            url, method=method, headers={"Host": host} if host is not None else {}
        )
    counted = 0

    def attempt(probe_timeout: float) -> bool:
        nonlocal counted
        counted += 1
        try:
            # The URL is always http(s)://127.0.0.1:<port><path>, built here; S310
            # guards against a caller-supplied scheme, which cannot occur.
            with opener.open(target, timeout=probe_timeout) as response:
                # With an expectation, it alone decides: an application told
                # to answer 204 is not healthy because it answered 200.
                if accept(response.status) if accept is not None else response.status == 200:
                    return True
                if on_attempt is not None:
                    on_attempt(
                        f"Health check attempt {counted} failed: "
                        f"HTTP {response.status} is not an expected status"
                    )
        except HTTPError as e:
            # An HTTP error status is still an answer; whether it is a
            # healthy one is the caller's call.
            if accept is not None and accept(e.code):
                return True
            if on_attempt is not None:
                on_attempt(f"Health check attempt {counted} failed: {e}")
        # A health check is a probe: any failure to reach the app means "not
        # ready yet", never "abort the deployment".
        except (URLError, OSError, ValueError) as e:
            if on_attempt is not None:
                on_attempt(f"Health check attempt {counted} failed: {e}")
        return False

    return _poll(attempt, retries=retries, delay=delay, within=within, clock=clock, sleep=sleep)


def wait_until_command_succeeds(
    run: Callable[[float], tuple[bool, str]],
    *,
    retries: int = 5,
    delay: float = 2.0,
    on_attempt: Callable[[str], None] | None = None,
    within: float | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    """
    Run a command until it exits 0, the attempts run out or time is up.

    The command check of an application: a program run as the application
    that says whether it is up. Waits exactly as :func:`wait_until_healthy`.

    Args:
        run: Runs the command once, given the seconds it may take, and says
            whether it exited 0 and what it printed.
        retries: Most attempts to make.
        delay: Seconds to wait between attempts.
        on_attempt: Called with a description of each failed attempt.
        within: Seconds of wall-clock time the whole poll may take.
        clock: Monotonic time source; :func:`time.monotonic` when None.
        sleep: How to pause; :func:`time.sleep` when None.

    Returns:
        True when the command succeeded.
    """
    counted = 0

    def attempt(probe_timeout: float) -> bool:
        nonlocal counted
        counted += 1
        ok, said = run(probe_timeout)
        if ok:
            return True
        if on_attempt is not None:
            on_attempt(f"Health check attempt {counted} failed: {said}")
        return False

    return _poll(
        attempt,
        retries=retries,
        delay=delay,
        within=within,
        clock=clock,
        sleep=sleep,
        attempt_timeout=COMMAND_TIMEOUT,
    )


def failure_output(result: CommandResult) -> str:
    """
    Combine what a failed command said, whichever stream it said it on.

    npm writes its real diagnosis to stdout and a summary to stderr; pip does
    the reverse. Showing only one of them is how "Build failed" ended up being
    the entire error message.

    Args:
        result: The failed command outcome.

    Returns:
        The combined output, stripped, or an empty string when there was none.
    """
    parts = [part for part in (result.stderr, result.stdout) if part and part.strip()]
    return "\n".join(part.strip() for part in parts)
