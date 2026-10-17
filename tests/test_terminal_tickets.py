"""
Tests for the terminal's settings and its one-time tickets.

A ticket is what turns "may open a terminal" (decided by an authenticated,
elevated call) into "is the person at this socket" (decided when the socket
presents it). Each property below is one way a stolen or replayed ticket
would otherwise open a root-equivalent shell.
"""

from __future__ import annotations

import pytest

from noust.core.config import DEFAULT_CONFIG
from noust.core.ens import profile
from noust.terminal import settings as terminal_settings
from noust.terminal.settings import SERVER_ENABLE_COMMAND, SERVER_FORCE_COMMAND, load_settings
from noust.terminal.tickets import (
    MAX_PENDING,
    TICKET_TTL_SECONDS,
    TicketRefused,
    TicketStore,
)


class FakeConfig:
    """A configuration that answers dotted keys from a flat dictionary."""

    def __init__(self, values: dict[str, object] | None = None, ens: bool = False) -> None:
        self.values = dict(values or {})
        if ens:
            self.values["security.profile"] = "ens-medium"

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


class Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def mint(store: TicketStore, **overrides):
    values = {
        "kind": "app",
        "domain": "shop.example.com",
        "service": None,
        "shell_name": None,
        "rows": 24,
        "cols": 80,
        "sid": "session-1",
        "actor": "maria",
        "client_ip": "203.0.113.7",
    }
    values.update(overrides)
    return store.mint(**values)


def redeem(store: TicketStore, ticket: str, **overrides):
    values = {
        "sid": "session-1",
        "actor": "maria",
        "client_ip": "203.0.113.7",
        "bind_address": True,
    }
    values.update(overrides)
    return store.redeem(ticket, **values)


class TestTheSettings:
    def test_the_defaults_in_the_code_are_the_defaults_of_the_configuration(self):
        section = DEFAULT_CONFIG["terminal"]
        defaults = load_settings(FakeConfig())

        assert defaults.enabled is section["enabled"]
        assert defaults.idle_seconds == section["idle_minutes"] * 60
        assert defaults.max_seconds == section["max_minutes"] * 60
        assert defaults.max_sessions == section["max_sessions"]
        assert defaults.max_sessions_per_user == section["max_sessions_per_user"]
        assert defaults.recording_max_bytes == section["recording"]["max_mb"] * 1024 * 1024
        assert defaults.server_enabled is section["server"]["enabled"]
        assert defaults.server_forced is section["server"]["force_ens"]
        assert defaults.exec_max_seconds == section["exec"]["max_seconds"]
        assert defaults.retention_days == DEFAULT_CONFIG["retention"]["terminal_days"]

    def test_the_root_shell_is_off_until_it_is_turned_on(self):
        settings = load_settings(FakeConfig())

        assert settings.server_allowed is False
        what, how = settings.server_refusal() or ("", "")
        assert "off" in what
        assert SERVER_ENABLE_COMMAND in how

    def test_turning_it_on_allows_it_outside_the_profile(self):
        settings = load_settings(FakeConfig({"terminal.server.enabled": True}))

        assert settings.server_allowed is True
        assert settings.server_refusal() is None

    def test_the_profile_keeps_it_off_even_when_it_is_turned_on(self):
        settings = load_settings(FakeConfig({"terminal.server.enabled": True}, ens=True))

        assert settings.server_allowed is False
        what, how = settings.server_refusal() or ("", "")
        assert "compliance controls" in what
        assert SERVER_FORCE_COMMAND in how

    def test_forcing_it_under_the_profile_allows_it(self):
        settings = load_settings(
            FakeConfig(
                {"terminal.server.enabled": True, "terminal.server.force_ens": True}, ens=True
            )
        )

        assert settings.server_allowed is True

    def test_forcing_without_enabling_allows_nothing(self):
        settings = load_settings(FakeConfig({"terminal.server.force_ens": True}, ens=True))

        assert settings.server_allowed is False

    def test_the_profile_caps_how_long_a_session_may_idle_and_last(self):
        loose = FakeConfig({"terminal.idle_minutes": 90, "terminal.max_minutes": 600}, ens=True)

        settings = load_settings(loose)

        assert settings.idle_seconds == profile.ENS_MEDIUM.terminal_idle_minutes * 60
        assert settings.max_seconds == profile.ENS_MEDIUM.terminal_max_minutes * 60

    def test_the_profile_lets_the_operator_tighten_them(self):
        tight = FakeConfig({"terminal.idle_minutes": 2, "terminal.max_minutes": 15}, ens=True)

        settings = load_settings(tight)

        assert (settings.idle_seconds, settings.max_seconds) == (120, 900)

    def test_a_recording_is_kept_as_long_as_the_audit_trail_under_the_profile(self):
        settings = load_settings(FakeConfig({"retention.terminal_days": 7}, ens=True))

        assert settings.retention_days == profile.ENS_MEDIUM.audit_retention_days
        assert load_settings(FakeConfig({"retention.terminal_days": 7})).retention_days == 7

    @pytest.mark.parametrize(
        "key", ["terminal.idle_minutes", "terminal.max_minutes", "terminal.max_sessions"]
    )
    @pytest.mark.parametrize("bad", ["soon", None, True, -3, 0, 10**9])
    def test_a_value_that_cannot_be_read_is_the_default_not_no_limit(self, key, bad):
        defaults = load_settings(FakeConfig())

        assert load_settings(FakeConfig({key: bad})) == defaults

    def test_the_profile_baseline_states_the_terminals_values(self):
        keys = {item.key for item in profile.baseline()}

        assert {"terminal.*", "terminal.server.enabled"} <= keys
        assert profile.server_terminal_allowed(FakeConfig()) is True
        assert profile.server_terminal_allowed(FakeConfig(ens=True)) is False

    def test_the_module_reads_nothing_but_the_configuration(self):
        assert terminal_settings.load_settings(FakeConfig()).enabled is True


class TestMintingAndRedeeming:
    def test_a_ticket_opens_exactly_what_was_authorised(self):
        store = TicketStore()
        ticket, minted = mint(store, domain="shop.example.com", service="web", shell_name="bash")

        grant = redeem(store, ticket)

        assert grant == minted
        assert (grant.kind, grant.domain, grant.service, grant.shell) == (
            "app",
            "shop.example.com",
            "web",
            "bash",
        )
        assert (grant.rows, grant.cols) == (24, 80)

    def test_the_ticket_is_long_random_and_never_the_session_id(self):
        store = TicketStore()
        first, grant = mint(store)
        second, _ = mint(store)

        assert first != second
        assert len(first) >= 40
        assert grant.session_id not in first
        assert len(grant.session_id) == 16 and int(grant.session_id, 16) >= 0

    def test_a_ticket_is_spent_by_its_first_use(self):
        store = TicketStore()
        ticket, _ = mint(store)
        redeem(store, ticket)

        with pytest.raises(TicketRefused) as refused:
            redeem(store, ticket)

        assert refused.value.code == "unknown"

    def test_a_ticket_that_nobody_minted_is_refused(self):
        with pytest.raises(TicketRefused) as refused:
            redeem(TicketStore(), "made-up")

        assert refused.value.code == "unknown"

    def test_an_empty_ticket_is_refused(self):
        with pytest.raises(TicketRefused):
            redeem(TicketStore(), "")

    def test_a_ticket_dies_after_its_few_seconds(self):
        clock = Clock()
        store = TicketStore(clock=clock)
        ticket, _ = mint(store)
        clock.now += TICKET_TTL_SECONDS + 1

        with pytest.raises(TicketRefused) as refused:
            redeem(store, ticket)

        assert refused.value.code == "expired"
        assert store.pending() == 0

    def test_a_ticket_still_inside_its_window_works(self):
        clock = Clock()
        store = TicketStore(clock=clock)
        ticket, _ = mint(store)
        clock.now += TICKET_TTL_SECONDS - 1

        assert redeem(store, ticket).domain == "shop.example.com"

    def test_the_ticket_lives_seconds_not_minutes(self):
        assert TICKET_TTL_SECONDS <= 30

    def test_another_signin_cannot_use_it(self):
        store = TicketStore()
        ticket, _ = mint(store, sid="session-1")

        with pytest.raises(TicketRefused) as refused:
            redeem(store, ticket, sid="session-2")

        assert refused.value.code == "other_credential"

    def test_another_person_behind_the_same_credential_cannot_use_it(self):
        # A central's fleet token is one credential for every operator on it.
        store = TicketStore()
        ticket, _ = mint(store, sid="fleet-token", actor="central on behalf of maria")

        with pytest.raises(TicketRefused) as refused:
            redeem(store, ticket, sid="fleet-token", actor="central on behalf of pedro")

        assert refused.value.code == "other_credential"

    def test_a_ticket_tried_by_the_wrong_credential_is_burned_for_the_right_one(self):
        store = TicketStore()
        ticket, _ = mint(store)
        with pytest.raises(TicketRefused):
            redeem(store, ticket, sid="thief")

        with pytest.raises(TicketRefused) as refused:
            redeem(store, ticket)

        assert refused.value.code == "unknown"

    def test_the_address_is_bound_when_the_console_binds_sessions_to_addresses(self):
        store = TicketStore()
        ticket, _ = mint(store, client_ip="203.0.113.7")

        with pytest.raises(TicketRefused) as refused:
            redeem(store, ticket, client_ip="198.51.100.9", bind_address=True)

        assert refused.value.code == "other_address"

    def test_the_address_is_not_bound_when_it_is_not_asked(self):
        store = TicketStore()
        ticket, _ = mint(store, client_ip="203.0.113.7")

        assert redeem(store, ticket, client_ip="198.51.100.9", bind_address=False)

    def test_a_signed_out_session_takes_its_tickets_with_it(self):
        store = TicketStore()
        kept, _ = mint(store, sid="other")
        gone, _ = mint(store, sid="session-1")
        mint(store, sid="session-1")

        assert store.revoke("session-1") == 2

        with pytest.raises(TicketRefused):
            redeem(store, gone)
        assert redeem(store, kept, sid="other")

    def test_only_a_hash_is_kept(self):
        store = TicketStore()
        ticket, _ = mint(store)

        assert all(ticket not in key for key in store._pending)

    def test_a_flood_of_minting_is_refused_before_it_fills_memory(self):
        store = TicketStore()
        for _ in range(MAX_PENDING):
            mint(store)

        with pytest.raises(TicketRefused) as refused:
            mint(store)

        assert refused.value.code == "busy"

    def test_expired_tickets_make_room(self):
        clock = Clock()
        store = TicketStore(clock=clock)
        for _ in range(MAX_PENDING):
            mint(store)
        clock.now += TICKET_TTL_SECONDS + 1

        assert mint(store)

    def test_an_unknown_kind_is_a_programming_error(self):
        with pytest.raises(ValueError):
            mint(TicketStore(), kind="database")


class TestComingBack:
    def test_a_ticket_to_resume_names_the_session_it_goes_back_to(self):
        store = TicketStore()
        ticket, grant = mint(store, resume="0123456789abcdef")

        assert grant.session_id == "0123456789abcdef"
        assert grant.resume == "0123456789abcdef"
        assert redeem(store, ticket).resume == "0123456789abcdef"

    def test_a_new_terminal_has_nothing_to_resume(self):
        _ticket, grant = mint(TicketStore())

        assert grant.resume is None

    @pytest.mark.parametrize("bad", ["", "../../etc", "0123456789ABCDEF", "short", "g" * 16])
    def test_something_that_is_not_a_session_id_is_refused(self, bad):
        with pytest.raises(ValueError):
            mint(TicketStore(), resume=bad)
