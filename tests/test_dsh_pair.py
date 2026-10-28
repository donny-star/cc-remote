"""The DSH pairing file holds a login token: it is private from creation."""
from __future__ import annotations

from cc_remote.wrapper.dsh_client import DshConnection
from cc_remote.wrapper.dsh_pair import save_connection
from tests.platform_assertions import assert_private_file, foreign_readable_dir

ORIGIN = "http://127.0.0.1:3080"
TOKEN = "a" * 43


def test_save_connection_is_private_under_a_foreign_readable_directory(tmp_path):
    foreign_readable_dir(tmp_path)
    path = tmp_path / "dsh.json"
    connection = DshConnection(ORIGIN, TOKEN)
    save_connection(connection, path)
    assert_private_file(path)
    assert DshConnection.load(path) == connection
    assert [entry.name for entry in tmp_path.iterdir()] == ["dsh.json"]


def test_save_connection_replaces_an_existing_file_privately(tmp_path):
    foreign_readable_dir(tmp_path)
    path = tmp_path / "dsh.json"
    path.write_text("stale")
    save_connection(DshConnection(ORIGIN, TOKEN), path)
    assert_private_file(path)
    assert DshConnection.load(path).token == TOKEN
