"""
Accounts: creation, sign-in, lockout, invitations, second factor, separation of duties.

The rules live in :class:`noust.core.accounts.AccountManager` and nowhere
else, so they are tested there, with a real store in a temporary directory
and a clock the tests move.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import totp
from noust.core.accounts import (
    AccountError,
    AccountManager,
    AccountNotFoundError,
    AuthenticationFailed,
    AuthPolicy,
    incompatible_roles,
    passwords,
)
from noust.core.accounts.policy import build_policy
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore

PASSWORD = "correct horse battery staple"


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, start: float = 1_900_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production cost makes every hash 50 ms; the rules do not depend on it."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def accounts(store: NoustStore, clock: Clock) -> AccountManager:
    return AccountManager(store, policy=AuthPolicy(), clock=clock)


def code_for(secret: str, clock: Clock) -> str:
    return totp.totp_now(secret, t=clock.now)


def enrol(accounts: AccountManager, account_id: int, clock: Clock) -> tuple[str, list[str]]:
    secret = accounts.begin_totp(account_id)
    codes = accounts.confirm_totp(account_id, code_for(secret, clock))
    assert codes is not None
    clock.now += 30
    return secret, codes


class TestPasswords:
    def test_the_hash_is_versioned_and_carries_its_parameters(self, monkeypatch):
        monkeypatch.setattr(passwords, "COST", (2**15, 8, 1))
        encoded = passwords.hash_password(PASSWORD)
        scheme, version, n, r, p, salt, key = encoded.split("$")
        assert (scheme, version, n, r, p) == ("scrypt", "v1", "32768", "8", "1")
        assert salt and key
        assert passwords.verify_password(PASSWORD, encoded)
        assert not passwords.verify_password(PASSWORD + "!", encoded)

    def test_a_missing_or_foreign_hash_never_verifies(self):
        assert not passwords.verify_password(PASSWORD, None)
        assert not passwords.verify_password(PASSWORD, "pbkdf2$whatever")
        assert not passwords.verify_password(PASSWORD, "scrypt$v1$3$8$1$AA$AA")

    def test_a_hash_of_older_parameters_asks_to_be_redone(self):
        old = passwords.hash_password(PASSWORD)
        assert not passwords.needs_rehash(old)
        assert passwords.needs_rehash(old.replace("$16$", "$32$", 1))

    @pytest.mark.parametrize(
        "candidate",
        ["short", "aaaaaaaaaaaaaaaa", "maria-is-my-password"],
    )
    def test_the_policy_refuses_easy_passwords(self, candidate):
        with pytest.raises(ValidationError):
            passwords.check_password_policy(candidate, username="maria", min_length=12)


class TestPolicy:
    def test_the_standard_profile_keeps_what_is_configured(self):
        policy = build_policy({"profile": "standard", "idle_minutes": 45, "absolute_hours": 24})
        assert (policy.idle_minutes, policy.absolute_hours, policy.token_max_days) == (45, 24, None)
        assert not policy.ens

    def test_the_ens_profile_caps_every_setting(self):
        policy = build_policy(
            {
                "profile": "ens-medium",
                "idle_minutes": 45,
                "absolute_hours": 24,
                "lockout_threshold": 10,
                "lockout_minutes": 1,
                "password_min_length": 8,
                "token_max_days": 365,
            }
        )
        assert policy.ens
        assert policy.idle_minutes == 15
        assert policy.absolute_hours == 8
        assert policy.lockout_threshold == 5
        assert policy.lockout_minutes == 15
        assert policy.password_min_length == 14
        assert policy.token_max_days == 90

    def test_an_unknown_profile_is_the_strict_one(self):
        assert build_policy({"profile": "ens_typo"}).ens

    def test_the_notice_version_follows_its_text(self):
        assert build_policy({}).notice_version is None
        first = build_policy({"notice_text": "Use is monitored."}).notice_version
        second = build_policy({"notice_text": "Use is monitored and logged."}).notice_version
        assert first and second and first != second


class TestCreate:
    def test_an_account_is_created_active_without_a_second_factor(self, accounts):
        account = accounts.create("Maria", "admin", password=PASSWORD, display_name="María")
        assert account.username == "maria"
        assert account.role == "admin"
        assert account.status == "active"
        assert account.has_password and not account.has_mfa
        assert accounts.any_exist() and accounts.count() == 1

    def test_the_name_is_unique_whatever_its_case(self, accounts):
        accounts.create("maria", "admin", password=PASSWORD)
        with pytest.raises(AccountError, match="already exists"):
            accounts.create("MARIA", "viewer", password=PASSWORD)

    @pytest.mark.parametrize("name", ["", "a", "root", "token:ci", "has space", "-lead"])
    def test_invalid_or_reserved_names_are_refused(self, accounts, name):
        with pytest.raises(ValidationError):
            accounts.create(name, "viewer", password=PASSWORD)

    def test_an_unknown_role_is_refused(self, accounts):
        with pytest.raises(ValidationError):
            accounts.create("maria", "superuser", password=PASSWORD)

    def test_a_weak_password_is_refused(self, accounts):
        with pytest.raises(ValidationError):
            accounts.create("maria", "viewer", password="short")


class TestSignIn:
    def test_the_right_password_signs_in_and_resets_the_counters(self, accounts, clock):
        account = accounts.create("maria", "operator", password=PASSWORD)
        with pytest.raises(AuthenticationFailed):
            accounts.authenticate("maria", "wrong", None, client_ip="203.0.113.9")
        signed = accounts.authenticate("maria", PASSWORD, None, client_ip="198.51.100.1")
        record = accounts.record_login(signed.id, "198.51.100.1")
        assert record.failures_since == 1
        assert record.last_failed_ip == "203.0.113.9"
        assert record.previous_login_at is None
        assert record.account.failures_since_login == 0
        clock.now += 60
        again = accounts.record_login(account.id, "198.51.100.2")
        assert again.previous_login_ip == "198.51.100.1"

    @pytest.mark.parametrize("scenario", ["unknown", "password", "code", "missing-code"])
    def test_every_refusal_is_the_same_error(self, accounts, clock, scenario):
        account = accounts.create("maria", "admin", password=PASSWORD)
        secret, _ = enrol(accounts, account.id, clock)
        name, password, code = "maria", PASSWORD, code_for(secret, clock)
        if scenario == "unknown":
            name = "nobody"
        elif scenario == "password":
            password = "not the password at all"
        elif scenario == "code":
            code = "000000" if code != "000000" else "111111"
        else:
            code = ""
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate(name, password, code, client_ip="203.0.113.9")
        assert caught.value.message == "Invalid credentials"
        assert caught.value.details == AuthenticationFailed("x").details

    def test_an_enrolled_account_needs_its_code(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        secret, _ = enrol(accounts, account.id, clock)
        assert (
            accounts.authenticate(
                "maria", PASSWORD, code_for(secret, clock), client_ip="198.51.100.1"
            ).id
            == account.id
        )

    def test_a_totp_code_is_not_accepted_twice_for_one_purpose(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        secret, _ = enrol(accounts, account.id, clock)
        code = code_for(secret, clock)
        accounts.authenticate("maria", PASSWORD, code, client_ip="198.51.100.1")
        with pytest.raises(AuthenticationFailed):
            accounts.authenticate("maria", PASSWORD, code, client_ip="198.51.100.1")
        assert accounts.verify_second_factor(account.id, code, purpose="elevate")

    def test_a_backup_code_works_once(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        _, codes = enrol(accounts, account.id, clock)
        assert len(codes) == 8 and all(len(code) == 11 for code in codes)
        assert accounts.authenticate("maria", PASSWORD, codes[0], client_ip="1.2.3.4")
        with pytest.raises(AuthenticationFailed):
            accounts.authenticate("maria", PASSWORD, codes[0], client_ip="1.2.3.4")
        assert accounts.get(account.id).backup_codes_remaining == 7

    def test_five_failures_lock_the_account_for_fifteen_minutes(self, accounts, clock):
        account = accounts.create("maria", "viewer", password=PASSWORD)
        for attempt in range(5):
            with pytest.raises(AuthenticationFailed):
                accounts.authenticate(
                    "maria", f"wrong {attempt}", None, client_ip=f"10.0.0.{attempt}"
                )
        locked = accounts.get(account.id)
        assert locked.is_locked(clock.now)
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate("maria", PASSWORD, None, client_ip="10.0.0.9")
        assert caught.value.reason == "locked"
        clock.now += 15 * 60 + 1
        assert accounts.authenticate("maria", PASSWORD, None, client_ip="10.0.0.9")

    def test_unlock_lifts_the_lock_before_it_runs_out(self, accounts, clock):
        account = accounts.create("maria", "viewer", password=PASSWORD)
        for _ in range(5):
            with pytest.raises(AuthenticationFailed):
                accounts.authenticate("maria", "wrong", None, client_ip="10.0.0.1")
        accounts.unlock("maria")
        assert accounts.get(account.id).status == "active"
        assert accounts.authenticate("maria", PASSWORD, None, client_ip="10.0.0.1")

    def test_a_disabled_account_cannot_sign_in(self, accounts):
        accounts.create("maria", "viewer", password=PASSWORD)
        accounts.disable("maria", reason="left the company")
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate("maria", PASSWORD, None, client_ip="10.0.0.1")
        assert caught.value.reason == "disabled"
        assert accounts.enable("maria").status == "active"

    def test_reset_mfa_removes_the_authenticator(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        enrol(accounts, account.id, clock)
        assert not accounts.reset_mfa("maria").has_mfa
        assert accounts.authenticate("maria", PASSWORD, None, client_ip="10.0.0.1")

    def test_a_second_authenticator_is_refused_while_one_is_enrolled(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        enrol(accounts, account.id, clock)
        with pytest.raises(AccountError):
            accounts.begin_totp(account.id)


class TestTwoSteps:
    """The console's sign-in: the password first, then the second factor on its own."""

    def test_the_password_step_does_not_ask_for_the_code(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        enrol(accounts, account.id, clock)
        signed = accounts.authenticate_password("maria", PASSWORD, client_ip="10.0.0.1")
        assert signed.id == account.id and signed.has_mfa
        assert accounts.get(account.id).failures_since_login == 0

    @pytest.mark.parametrize("scenario", ["unknown", "password", "disabled", "invited"])
    def test_the_password_step_refuses_the_same_way(self, accounts, scenario):
        accounts.create("maria", "admin", password=PASSWORD)
        accounts.invite("lucia", "viewer")
        name, password = "maria", PASSWORD
        if scenario == "unknown":
            name = "nobody"
        elif scenario == "password":
            password = "not the password at all"
        elif scenario == "disabled":
            accounts.disable("maria")
        else:
            name = "lucia"
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate_password(name, password, client_ip="10.0.0.1")
        assert caught.value.message == "Invalid credentials"

    def test_the_second_step_checks_the_code_and_counts_a_wrong_one(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        secret, _ = enrol(accounts, account.id, clock)
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.complete_second_factor(account.id, "000000", client_ip="10.0.0.1")
        assert caught.value.reason == "bad_code"
        assert accounts.get(account.id).failures_since_login == 1
        signed = accounts.complete_second_factor(
            account.id, code_for(secret, clock), client_ip="10.0.0.1"
        )
        assert signed.id == account.id

    def test_the_second_step_is_held_to_the_per_account_lockout(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        secret, _ = enrol(accounts, account.id, clock)
        for _ in range(5):
            with pytest.raises(AuthenticationFailed):
                accounts.complete_second_factor(account.id, "000000", client_ip="10.0.0.1")
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.complete_second_factor(
                account.id, code_for(secret, clock), client_ip="10.0.0.1"
            )
        assert caught.value.reason == "locked"

    def test_the_second_step_fails_closed_without_a_second_factor(self, accounts):
        account = accounts.create("maria", "admin", password=PASSWORD)
        with pytest.raises(AuthenticationFailed):
            accounts.complete_second_factor(account.id, "000000", client_ip="10.0.0.1")


class TestSignInByEmail:
    """The e-mail of the person an account belongs to also names it, when it names one."""

    def test_the_email_of_one_account_signs_it_in(self, accounts):
        account = accounts.create(
            "yago", "admin", password=PASSWORD, person_ref="Yago.Lopez@example.com"
        )
        signed = accounts.authenticate(
            " YAGO.lopez@example.com ", PASSWORD, None, client_ip="10.0.0.1"
        )
        assert signed.id == account.id

    def test_a_person_with_two_accounts_uses_the_username(self, accounts):
        accounts.create("yago", "operator", password=PASSWORD, person_ref="yago@example.com")
        accounts.create("yago.view", "viewer", password=PASSWORD, person_ref="yago@example.com")
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate("yago@example.com", PASSWORD, None, client_ip="10.0.0.1")
        assert caught.value.reason == "unknown_account"
        assert caught.value.message == "Invalid credentials"
        assert accounts.authenticate("yago.view", PASSWORD, None, client_ip="10.0.0.1")

    def test_only_accounts_that_may_sign_in_are_counted(self, accounts):
        accounts.create("yago", "operator", password=PASSWORD, person_ref="yago@example.com")
        accounts.create("yago.old", "viewer", password=PASSWORD, person_ref="yago@example.com")
        accounts.disable("yago.old")
        accounts.invite("yago.new", "viewer", person_ref="yago@example.com")
        signed = accounts.authenticate("yago@example.com", PASSWORD, None, client_ip="10.0.0.1")
        assert signed.username == "yago"

    def test_a_wrong_password_by_email_counts_on_the_account(self, accounts):
        account = accounts.create(
            "yago", "operator", password=PASSWORD, person_ref="yago@example.com"
        )
        with pytest.raises(AuthenticationFailed) as caught:
            accounts.authenticate("yago@example.com", "nope", None, client_ip="10.0.0.1")
        assert caught.value.reason == "bad_password"
        assert accounts.get(account.id).failures_since_login == 1

    def test_a_reference_that_is_not_an_email_is_not_a_sign_in_name(self, accounts):
        accounts.create("yago", "operator", password=PASSWORD, person_ref="emp-4521")
        with pytest.raises(AuthenticationFailed):
            accounts.authenticate("emp-4521", PASSWORD, None, client_ip="10.0.0.1")


class TestInvitations:
    def test_an_invitation_is_single_use_and_activates_the_account(self, accounts, clock):
        account, code = accounts.invite("lucia", "operator", created_by="maria")
        assert account.status == "invited" and code.startswith("noust_inv_")
        with pytest.raises(AuthenticationFailed):
            accounts.authenticate("lucia", PASSWORD, None, client_ip="10.0.0.1")
        opened, secret = accounts.open_invitation(code)
        assert opened.username == "lucia"
        assert accounts.open_invitation(code)[1] == secret
        active, backup = accounts.accept_invitation(code, PASSWORD, code_for(secret, clock))
        assert active.status == "active" and active.has_mfa and len(backup) == 8
        with pytest.raises(AuthenticationFailed):
            accounts.open_invitation(code)

    def test_an_expired_invitation_is_refused(self, accounts, clock):
        _, code = accounts.invite("lucia", "viewer", expires_hours=1)
        clock.now += 3601
        with pytest.raises(AuthenticationFailed):
            accounts.open_invitation(code)

    def test_a_new_invitation_retires_the_previous_one(self, accounts):
        _, first = accounts.invite("lucia", "viewer")
        _, second = accounts.invite("lucia")
        with pytest.raises(AuthenticationFailed):
            accounts.open_invitation(first)
        assert accounts.open_invitation(second)

    def test_a_wrong_authenticator_code_keeps_the_invitation(self, accounts, clock):
        _, code = accounts.invite("lucia", "viewer")
        _, secret = accounts.open_invitation(code)
        wrong = "000000" if code_for(secret, clock) != "000000" else "111111"
        with pytest.raises(ValidationError):
            accounts.accept_invitation(code, PASSWORD, wrong)
        assert accounts.accept_invitation(code, PASSWORD, code_for(secret, clock))

    def test_only_the_stored_digest_of_the_code_is_kept(self, accounts, store):
        _, code = accounts.invite("lucia", "viewer")
        stored = (
            store._get_connection().execute("SELECT code_hash FROM account_invitations").fetchone()
        )
        assert code not in stored[0]

    def test_an_existing_account_recovers_through_an_invitation(self, accounts, clock):
        accounts.create("maria", "admin", password=PASSWORD)
        _, code = accounts.invite("maria")
        _, secret = accounts.open_invitation(code)
        accounts.accept_invitation(code, "a brand new long passphrase", code_for(secret, clock))
        clock.now += 30
        assert accounts.authenticate(
            "maria", "a brand new long passphrase", code_for(secret, clock), client_ip="10.0.0.1"
        )


class TestSeparationOfDuties:
    @pytest.mark.parametrize(
        ("first", "second", "clash"),
        [
            ("admin", "security", True),
            ("operator", "security", True),
            ("auditor", "viewer", True),
            ("auditor", "admin", True),
            ("admin", "operator", False),
            ("viewer", "security", False),
            ("admin", "admin", False),
        ],
    )
    def test_the_incompatibility_table(self, first, second, clash):
        assert incompatible_roles(first, second) is clash
        assert incompatible_roles(second, first) is clash

    def test_one_person_cannot_hold_admin_and_security(self, accounts):
        accounts.create("maria", "admin", password=PASSWORD, person_ref="maria@example.com")
        with pytest.raises(AccountError, match="incompatible"):
            accounts.create(
                "maria.sec", "security", password=PASSWORD, person_ref="MARIA@example.com"
            )

    def test_changing_a_role_is_checked_too(self, accounts):
        accounts.create("maria", "admin", password=PASSWORD, person_ref="maria@example.com")
        accounts.create("maria.view", "viewer", password=PASSWORD, person_ref="maria@example.com")
        with pytest.raises(AccountError):
            accounts.set_role("maria.view", "security")

    def test_a_documented_exception_allows_it_until_it_expires(self, accounts, clock):
        accounts.create("maria", "admin", password=PASSWORD, person_ref="maria@example.com")
        exception = accounts.add_exception(
            "maria@example.com", "Single responsible person at a small office", days=30
        )
        assert accounts.create(
            "maria.sec", "security", password=PASSWORD, person_ref="maria@example.com"
        )
        assert accounts.separation_conflicts()[0]["exception"] is True
        accounts.revoke_exception(exception.id)
        assert accounts.separation_conflicts()[0]["exception"] is False

    def test_an_exception_needs_a_reason_and_an_end(self, accounts):
        with pytest.raises(ValidationError):
            accounts.add_exception("maria@example.com", "short", days=30)
        with pytest.raises(ValidationError):
            accounts.add_exception("maria@example.com", "A reason long enough", days=0)


class TestLifecycle:
    def test_remove_deletes_the_account(self, accounts):
        accounts.create("maria", "viewer", password=PASSWORD)
        accounts.remove("maria")
        assert accounts.find("maria") is None
        with pytest.raises(AccountNotFoundError):
            accounts.remove("maria")

    def test_the_first_admin_is_the_oldest_usable_one(self, accounts, clock):
        accounts.create("zed", "viewer", password=PASSWORD)
        clock.now += 1
        first = accounts.create("maria", "admin", password=PASSWORD)
        clock.now += 1
        accounts.create("lucia", "admin", password=PASSWORD)
        assert accounts.first_admin().id == first.id
        accounts.disable("maria")
        assert accounts.first_admin().username == "lucia"

    def test_change_password_needs_the_current_one(self, accounts):
        account = accounts.create("maria", "viewer", password=PASSWORD)
        with pytest.raises(AuthenticationFailed):
            accounts.change_password(account.id, "wrong", "another long passphrase", client_ip="x")
        accounts.change_password(account.id, PASSWORD, "another long passphrase", client_ip="x")
        assert accounts.authenticate("maria", "another long passphrase", None, client_ip="x")

    def test_the_notice_is_accepted_by_version(self, store, clock):
        policy = AuthPolicy(notice_text="Use of this console is logged.")
        manager = AccountManager(store, policy=policy, clock=clock)
        account = manager.create("maria", "viewer", password=PASSWORD)
        with pytest.raises(ValidationError):
            manager.accept_notice(account.id, "stale")
        accepted = manager.accept_notice(account.id, policy.notice_version or "")
        assert accepted.notice_version == policy.notice_version

    def test_the_account_record_carries_no_secret(self, accounts, clock):
        account = accounts.create("maria", "admin", password=PASSWORD)
        enrol(accounts, account.id, clock)
        public = accounts.get(account.id).to_dict()
        assert "password_hash" not in public and "totp_secret" not in public
        assert public["mfa_enabled"] is True
