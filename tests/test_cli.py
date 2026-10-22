"""mail.py sends what the user named and nothing else, and remembers what it sent.

The service decides; this side only shapes a request. A file is read here with the
caller's own access and travels as bytes, so the service never opens a path.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("mail_cli", ROOT / "mail.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def answering(monkeypatch, answer):
    asked = []

    def ask(socket, request):
        asked.append(request)
        return answer

    monkeypatch.setattr(cli, "ask", ask)
    return asked


def test_only_named_fields_are_sent(monkeypatch, capsys):
    asked = answering(monkeypatch, {"ok": True, "result": []})
    assert cli.main(["read", "--mailbox", "work", "--limit", "5"]) == 0
    assert asked == [{"action": "read", "mailbox": "work", "limit": 5}]


def test_lists_and_flags_take_their_shape(monkeypatch):
    asked = answering(monkeypatch, {"ok": True, "result": {}})
    cli.main(["send", "--mailbox", "work", "--to", "a@x.org", "b@x.org", "--bcc", "c@x.org", "--subject", "s", "--text", "t"])
    assert asked[0]["to"] == ["a@x.org", "b@x.org"] and asked[0]["bcc"] == ["c@x.org"]
    cli.main(["reply", "--mailbox", "work", "--uid", "7", "--all", "--text", "t"])
    assert asked[1]["all"] is True and asked[1]["uid"] == 7
    cli.main(["mark-read", "--mailbox", "work", "--uids", "1", "2", "--unread"])
    assert asked[2]["uids"] == [1, 2] and asked[2]["seen"] is False


def test_a_file_travels_as_bytes(monkeypatch, tmp_path):
    asked = answering(monkeypatch, {"ok": True, "result": {}})
    attached = tmp_path / "report.pdf"
    attached.write_bytes(b"%PDF-1")
    cli.main(["send", "--mailbox", "work", "--to", "a@x.org", "--file", str(attached)])
    assert asked[0]["files"] == [{"name": "report.pdf", "data": base64.b64encode(b"%PDF-1").decode()}]


def test_the_text_may_come_from_a_file(monkeypatch, tmp_path):
    asked = answering(monkeypatch, {"ok": True, "result": {}})
    body = tmp_path / "body.txt"
    body.write_text("line one\nline two\n")
    cli.main(["send", "--mailbox", "work", "--to", "a@x.org", "--text-file", str(body)])
    assert asked[0]["text"] == "line one\nline two\n"


def test_a_send_is_remembered_by_its_message_id(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_SENT_LOG", str(tmp_path / "sent"))
    answering(monkeypatch, {"ok": True, "result": {"message_id": "<1@example.org>"}})
    cli.main(["send", "--mailbox", "work", "--to", "a@x.org"])
    assert cli.SENT.ids("work") == {"<1@example.org>"}
    assert cli.SENT.ids("other") == set()


def test_a_refusal_exits_one_and_prints_it(monkeypatch, capsys):
    answering(monkeypatch, {"ok": False, "error": "no", "remedy": "add read"})
    assert cli.main(["read", "--mailbox", "work"]) == 1
    assert json.loads(capsys.readouterr().out)["remedy"] == "add read"


def test_an_unreachable_service_exits_two(capsys):
    assert cli.main(["permissions", "--socket", "/nonexistent/socket"]) == 2
    assert "/nonexistent/socket" in capsys.readouterr().err
