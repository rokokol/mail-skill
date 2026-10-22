"""The service starts from a non-secret mailbox file and passwords systemd hands it.

A password never travels through an argument or the environment. A mailbox whose
password is missing is a configuration fault that a restart cannot fix, so the service
stops on the code the unit is told not to restart.
"""

import json

import pytest

from mail_agentd import __main__ as main


def config(tmp_path, **mailboxes):
    path = tmp_path / "mailboxes.json"
    path.write_text(json.dumps(mailboxes))
    return path


WORK = {
    "address": "work@example.org",
    "user": "work@example.org",
    "imap_host": "mail.example.org",
    "smtp_host": "mail.example.org",
    "own": [r"/gh-.*@example\.org/"],
}


def test_the_help_prints_the_vocabulary(capsys):
    with pytest.raises(SystemExit):
        main.main(["--help"])
    printed = capsys.readouterr().out
    for word in ("read", "expunge", "send", "draft", "list", "all"):
        assert word in printed


def test_mailboxes_are_built_with_their_passwords(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "imap-password-work").write_text("imap-secret\n")
    (credentials / "smtp-password-work").write_text("smtp-secret\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    boxes = main.mailboxes(config(tmp_path, work=WORK))
    box = boxes["work"]
    assert box.config.address == "work@example.org"
    assert box.config.own == [r"/gh-.*@example\.org/"]
    assert box._imap_password == "imap-secret"
    assert box._smtp_password == "smtp-secret"


def test_a_private_config_carries_its_own_password_and_the_rest_defaults(tmp_path, monkeypatch):
    # The file a user writes by hand into a secret: only what differs from the defaults
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    written = {"address": "w@r.art", "imap_host": "mail.r.art", "password": "in-the-file"}
    box = main.mailboxes(config(tmp_path, work=written))["work"]
    assert box.config.user == "w@r.art"
    assert box.config.smtp_host == "mail.r.art"
    assert (box.config.imap_port, box.config.smtp_port) == (993, 465)
    assert box._imap_password == box._smtp_password == "in-the-file"


def test_a_field_the_service_does_not_know_stops_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    misspelt = {"address": "w@r.art", "imap_host": "m", "password": "x", "imap_hots": "m"}
    with pytest.raises(SystemExit) as stopped:
        main.mailboxes(config(tmp_path, work=misspelt))
    assert stopped.value.code == main.EX_CONFIG


def test_a_missing_password_stops_the_service_for_good(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    with pytest.raises(SystemExit) as stopped:
        main.mailboxes(config(tmp_path, work=WORK))
    assert stopped.value.code == main.EX_CONFIG


def test_a_mailbox_name_a_permission_file_cannot_carry_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        main.mailboxes(config(tmp_path, **{"../x": WORK}))
    assert stopped.value.code == main.EX_CONFIG


def test_an_own_rule_that_does_not_parse_is_refused_at_start(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "imap-password-work").write_text("x")
    (credentials / "smtp-password-work").write_text("x")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    with pytest.raises(SystemExit) as stopped:
        main.mailboxes(config(tmp_path, work=WORK | {"own": ["/(broken/"]}))
    assert stopped.value.code == main.EX_CONFIG
