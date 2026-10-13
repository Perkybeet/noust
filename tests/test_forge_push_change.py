"""
What a push delivery says about the commits it carries (checklist A-13, A-28).

Each forge lists a push's commits in its own JSON; the skip markers read their
messages and the watch paths read the files they changed. Where a forge does
not say (Bitbucket sends no file lists) or says only part of it, the answer
is "unknown", and unknown always deploys.
"""

from __future__ import annotations

from noust.core.forge_events import PushChange, parse_push_change


def _commit(message: str, added=(), modified=(), removed=()) -> dict:
    return {
        "id": "a" * 40,
        "message": message,
        "added": list(added),
        "modified": list(modified),
        "removed": list(removed),
    }


class TestGitHubAndGitea:
    def test_messages_and_the_union_of_files(self) -> None:
        payload = {
            "commits": [
                _commit("one", added=["a.txt"], modified=["b.txt"]),
                _commit("two [skip ci]", removed=["c.txt"], modified=["b.txt"]),
            ]
        }
        for provider in ("github", "gitea"):
            change = parse_push_change(provider, payload)
            assert change.messages == ("one", "two [skip ci]")
            assert change.files == ("a.txt", "b.txt", "c.txt")
            assert change.messages_complete is True

    def test_a_push_with_no_commit_list_has_unknown_files(self) -> None:
        change = parse_push_change("github", {"ref": "refs/heads/new", "commits": []})
        assert change.messages == ()
        assert change.files is None

    def test_commits_with_no_file_lists_at_all_are_unknown_not_empty(self) -> None:
        change = parse_push_change("github", {"commits": [{"message": "merge"}]})
        assert change.files is None

    def test_head_commit_is_used_when_the_list_is_missing(self) -> None:
        payload = {"head_commit": {"message": "only [skip cd]", "modified": ["x"]}}
        change = parse_push_change("github", payload)
        assert change.messages == ("only [skip cd]",)
        assert change.files == ("x",)

    def test_garbage_is_ignored(self) -> None:
        change = parse_push_change(
            "github",
            {"commits": ["nope", {"message": 5, "added": "x", "modified": [1, "ok.txt"]}]},
        )
        assert change.messages == ()
        assert change.files == ("ok.txt",)

    def test_a_list_at_the_forge_cap_may_be_cut_short(self) -> None:
        payload = {"commits": [_commit(f"c{i}", modified=[f"f{i}"]) for i in range(2048)]}
        change = parse_push_change("github", payload)
        assert change.messages_complete is False
        assert change.files is None


class TestGitLab:
    def test_reads_the_commits(self) -> None:
        payload = {
            "total_commits_count": 2,
            "commits": [
                _commit("x [skip ci]", added=["a"]),
                _commit("y [skip ci]", modified=["b"]),
            ],
        }
        change = parse_push_change("gitlab", payload)
        assert change.messages == ("x [skip ci]", "y [skip ci]")
        assert change.files == ("a", "b")
        assert change.messages_complete is True

    def test_more_commits_than_listed_is_cut_short(self) -> None:
        payload = {"total_commits_count": 25, "commits": [_commit("x", added=["a"])] * 20}
        change = parse_push_change("gitlab", payload)
        assert change.messages_complete is False
        assert change.files is None


class TestBitbucket:
    def _push(self, *changes: dict) -> dict:
        return {"push": {"changes": list(changes)}}

    def test_messages_from_every_change_and_no_files(self) -> None:
        payload = self._push(
            {"new": {"type": "branch", "name": "main"}, "commits": [{"message": "a [skip ci]"}]},
            {"new": {"type": "branch", "name": "dev"}, "commits": [{"message": "b [skip ci]"}]},
        )
        change = parse_push_change("bitbucket", payload)
        assert change.messages == ("a [skip ci]", "b [skip ci]")
        assert change.files is None
        assert change.messages_complete is True

    def test_a_truncated_change_is_cut_short(self) -> None:
        payload = self._push(
            {
                "new": {"type": "branch", "name": "main"},
                "truncated": True,
                "commits": [{"message": "a [skip ci]"}],
            }
        )
        assert parse_push_change("bitbucket", payload).messages_complete is False

    def test_an_unknown_provider_knows_nothing(self) -> None:
        assert parse_push_change("nowhere", {"commits": [_commit("x")]}) == PushChange()
