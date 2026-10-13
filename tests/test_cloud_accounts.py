"""Cloud accounts: tokens kept as secrets, checked before they are kept, never shown."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.exceptions import CloudProviderError, NodeError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.fleet.cloud.accounts import CloudAccounts, check_token
from tests.cloud_support import FakeApi

TOKEN = "hc" + "_" + "a" * 20 + "Z9k2"
OTHER = "hc" + "_" + "b" * 20 + "Q7m1"


@pytest.fixture
def api() -> FakeApi:
    fake = FakeApi(prefix="/v1")
    fake.on("GET", "/locations", (200, {"locations": []}))
    return fake


@pytest.fixture
def accounts(tmp_path: Path, api: FakeApi) -> CloudAccounts:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    return CloudAccounts(
        store,
        SecretStore(root=tmp_path / "secrets"),
        provider_options={"opener": api, "sleep": lambda _s: None},
    )


class TestAddingAnAccount:
    def test_a_token_the_provider_accepts_is_kept_as_a_secret_and_never_in_the_row(
        self, accounts: CloudAccounts, tmp_path: Path
    ):
        account = accounts.add("hetzner-prod", "hetzner", TOKEN, note="project web", actor="ana")

        assert account.provider == "hetzner"
        assert account.token_tail == TOKEN[-4:]
        assert account.verified_at is not None
        assert accounts.secrets.read("cloud-accounts/hetzner-prod") == TOKEN
        assert (
            tmp_path / "secrets" / "cloud-accounts" / "hetzner-prod"
        ).stat().st_mode & 0o777 == 0o600
        database = (tmp_path / "noust.db").read_bytes()
        assert TOKEN.encode() not in database

    def test_the_listing_and_the_json_show_only_the_end_of_the_token(self, accounts: CloudAccounts):
        accounts.add("a", "hetzner", TOKEN)

        shown = json.dumps([a.to_dict() for a in accounts.list()])

        assert TOKEN not in shown
        assert "...Z9k2" in shown

    def test_a_token_the_provider_refuses_is_not_kept(self, accounts: CloudAccounts, api: FakeApi):
        api._routes.clear()
        api.on("GET", "/locations", (401, {"error": {"message": "unable to authenticate"}}))

        with pytest.raises(CloudProviderError):
            accounts.add("a", "hetzner", TOKEN)

        assert accounts.list() == []
        assert accounts.secrets.read("cloud-accounts/a") is None

    def test_a_name_in_use_is_refused_without_touching_the_provider(
        self, accounts: CloudAccounts, api: FakeApi
    ):
        accounts.add("a", "hetzner", TOKEN)
        before = len(api.calls)

        with pytest.raises(NodeError) as caught:
            accounts.add("a", "hetzner", OTHER)

        assert "already exists" in caught.value.message
        assert len(api.calls) == before
        assert accounts.secrets.read("cloud-accounts/a") == TOKEN

    @pytest.mark.parametrize(
        "bad", ["", "short", "has space in it 1234567890", "line\nbreak1234567890", "x" * 600]
    )
    def test_a_malformed_token_is_refused_before_any_request(self, bad: str):
        with pytest.raises(ValidationError):
            check_token(bad)

    @pytest.mark.parametrize("name", ["", "Upper", "a b", "../x", "x" * 40, "-a"])
    def test_a_bad_name_is_refused(self, accounts: CloudAccounts, name: str):
        with pytest.raises(ValidationError):
            accounts.add(name, "hetzner", TOKEN)

    def test_an_unknown_provider_is_refused(self, accounts: CloudAccounts):
        with pytest.raises(ValidationError):
            accounts.add("a", "aws", TOKEN)

    def test_a_token_pasted_with_a_trailing_newline_is_cleaned(self, accounts: CloudAccounts):
        accounts.add("a", "hetzner", TOKEN + "\n")

        assert accounts.secrets.read("cloud-accounts/a") == TOKEN

    def test_a_note_must_be_one_short_line(self, accounts: CloudAccounts):
        with pytest.raises(ValidationError):
            accounts.add("a", "hetzner", TOKEN, note="two\nlines")

    def test_it_can_be_added_without_asking_the_provider(
        self, accounts: CloudAccounts, api: FakeApi
    ):
        api._routes.clear()

        account = accounts.add("a", "hetzner", TOKEN, verify=False)

        assert account.verified_at is None
        assert api.calls == []


class TestTheAdapterAnAccountBuilds:
    def test_it_holds_the_token_and_sends_it_as_a_bearer(
        self, accounts: CloudAccounts, api: FakeApi
    ):
        accounts.add("a", "hetzner", TOKEN)

        accounts.provider("a").verify()

        assert api.calls[-1].headers["authorization"] == f"Bearer {TOKEN}"

    def test_a_missing_secret_file_says_how_to_put_the_token_back(self, accounts: CloudAccounts):
        accounts.add("a", "hetzner", TOKEN)
        accounts.secrets.delete("cloud-accounts/a")

        with pytest.raises(NodeError) as caught:
            accounts.provider("a")

        assert "token" in caught.value.details

    def test_an_unknown_account_names_the_command_that_lists_them(self, accounts: CloudAccounts):
        with pytest.raises(NodeError) as caught:
            accounts.provider("nope")

        assert "account list" in caught.value.details


class TestReplacingAndRemoving:
    def test_a_new_token_replaces_the_old_one_only_after_the_provider_accepts_it(
        self, accounts: CloudAccounts, api: FakeApi
    ):
        accounts.add("a", "hetzner", TOKEN)
        api._routes.clear()
        api.on("GET", "/locations", (401, {"error": {"message": "no"}}))

        with pytest.raises(CloudProviderError):
            accounts.replace_token("a", OTHER)

        assert accounts.secrets.read("cloud-accounts/a") == TOKEN
        api._routes.clear()
        api.on("GET", "/locations", (200, {"locations": []}))
        updated = accounts.replace_token("a", OTHER)
        assert accounts.secrets.read("cloud-accounts/a") == OTHER
        assert updated.token_tail == OTHER[-4:]

    def test_removing_deletes_the_secret_and_the_row(self, accounts: CloudAccounts):
        accounts.add("a", "hetzner", TOKEN)

        accounts.remove("a")

        assert accounts.list() == []
        assert accounts.secrets.read("cloud-accounts/a") is None

    def test_an_account_with_servers_is_not_removed_unless_forced(self, accounts: CloudAccounts):
        accounts.add("a", "hetzner", TOKEN)
        with accounts.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO cloud_servers (node, account, provider, provider_id, state, "
                "created_at, updated_at) VALUES ('web-2', 'a', 'hetzner', '1', 'ready', 't', 't')"
            )

        with pytest.raises(NodeError) as caught:
            accounts.remove("a")

        assert "web-2" in caught.value.message
        assert accounts.secrets.read("cloud-accounts/a") == TOKEN
        accounts.remove("a", force=True)
        assert accounts.list() == []

    def test_a_server_already_deleted_does_not_hold_the_account(self, accounts: CloudAccounts):
        accounts.add("a", "hetzner", TOKEN)
        with accounts.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO cloud_servers (node, account, provider, provider_id, state, "
                "created_at, updated_at) VALUES ('web-2', 'a', 'hetzner', '1', 'deleted', 't', 't')"
            )

        accounts.remove("a")

        assert accounts.list() == []


class TestWhatIsAudited:
    def test_the_events_name_the_account_and_never_the_token(
        self, accounts: CloudAccounts, tmp_path: Path
    ):
        from noust.core import audit

        accounts.add("a", "hetzner", TOKEN)
        accounts.replace_token("a", OTHER)
        accounts.remove("a")

        text = Path(audit.get_log().path).read_text()
        assert "fleet.cloud.account.add" in text
        assert "fleet.cloud.account.rotate" in text
        assert "fleet.cloud.account.remove" in text
        assert TOKEN not in text
        assert OTHER not in text
