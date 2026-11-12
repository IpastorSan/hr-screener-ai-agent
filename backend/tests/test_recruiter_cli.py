"""`sazon add-recruiter` and `sazon set-password`: an admin's way in, with no self-service."""

import io
from pathlib import Path

import pytest

from sazon import recruiters
from sazon.auth import verify_password
from sazon.cli import main
from sazon.storage.repo import Repo, make_engine


def test_add_a_recruiter_then_set_their_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    url = f"sqlite:///{tmp_path / 'sazon.db'}"
    email = "nueva.reclutadora@gruposazon.example"
    args = ["--db", url, "add-recruiter", "--email", email, "--name", "Nueva", "--country", "MX"]
    assert main([*args, "--admin", "--locations", "mx-gdl-centro"]) == 0
    assert main(args) == 1  # the email is taken

    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert main(["--db", url, "set-password", "--email", email, "--stdin"]) == 2

    monkeypatch.setattr("sys.stdin", io.StringIO("una clave larga y nueva\n"))
    assert main(["--db", url, "set-password", "--email", email, "--stdin"]) == 0
    assert main(["--db", url, "set-password", "--email", "nadie@x.com", "--stdin"]) == 1

    recruiter = recruiters.by_email(Repo(make_engine(url)), email)
    assert recruiter is not None
    assert recruiter.is_admin
    assert recruiter.location_ids == ["mx-gdl-centro"]
    assert recruiter.session_version == 1
    assert verify_password(recruiter.password_hash, "una clave larga y nueva")
    assert "una clave larga y nueva" not in capsys.readouterr().out
