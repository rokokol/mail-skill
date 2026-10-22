"""mail-watch.py follows a mailbox: subscribe, catch up by UID, then each pushed message.

The mark is a UID within a UIDVALIDITY, so a message is shown once whether it came in the
catch-up read or the stream. What the agent sent itself is left out by its Message-ID.
A service that restarts is waited out, and a rebuilt INBOX starts the mark over.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("mail_watch", ROOT / "mail-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
cli = watch.mail


class Options:
    def __init__(self, **fields):
        self.mailbox = "work"
        self.socket = "/nowhere"
        self.interval = 0
        self.once = False
        self.state = None
        self.exec = None
        for name, value in fields.items():
            setattr(self, name, value)


def message(uid, sender="boss@corp.com", subject="hi", message_id=None):
    return {
        "uid": uid,
        "uidvalidity": 1000,
        "from": [{"name": "", "address": sender}],
        "subject": subject,
        "message_id": message_id or f"<{uid}@corp.com>",
    }


def serve(monkeypatch, held, pushed=(), last=None, uidvalidity=1000):
    """Answer reads from `held`, a subscription with `pushed`, and record the order."""
    order = []

    def ask(socket, request):
        order.append("read")
        since = request.get("since_uid")
        shown = [m for m in held if since is None or m["uid"] > since]
        if since is None:
            shown = shown[-request.get("limit", 20) :]
        top = max([m["uid"] for m in held], default=since)
        return {
            "ok": True,
            "result": {
                "uidvalidity": uidvalidity,
                "messages": shown,
                "more": False,
                "last_uid": last if last is not None else top,
            },
        }

    def stream(socket, request):
        order.append("subscribe")
        yield {"ok": True, "result": {"mailbox": "work"}}
        for item in pushed:
            yield item

    monkeypatch.setattr(cli, "ask", ask)
    monkeypatch.setattr(cli, "stream", stream)
    return order


def test_the_subscription_opens_before_the_catch_up_read(monkeypatch, capsys):
    order = serve(monkeypatch, [message(5)])
    watch.follow(Options(), (1000, 4))
    assert order == ["subscribe", "read"]


def test_a_message_in_both_the_read_and_the_stream_is_shown_once(monkeypatch, capsys):
    serve(monkeypatch, [message(5)], pushed=[{"ok": True, "message": message(5)}, {"ok": True, "message": message(6)}])
    mark, _ = watch.follow(Options(), (1000, 4))
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == ["5", "6"]
    assert mark == (1000, 6)


def test_a_line_names_uid_sender_and_subject_on_one_line(monkeypatch, capsys):
    serve(monkeypatch, [message(5, subject="two\nlines")])
    watch.follow(Options(), (1000, 4))
    assert capsys.readouterr().out == "5 boss@corp.com two lines\n"


def test_the_agents_own_send_is_left_out(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MAIL_SENT_LOG", str(tmp_path / "sent"))
    cli.SENT.remember("work", "<mine@example.org>")
    serve(monkeypatch, [message(5, message_id="<mine@example.org>"), message(6)])
    watch.follow(Options(), (1000, 4))
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ["6"]


def test_the_mark_is_kept_after_each_message(monkeypatch, tmp_path):
    state = tmp_path / "state"
    serve(monkeypatch, [], pushed=[{"ok": True, "message": message(7)}])
    watch.follow(Options(state=str(state)), (1000, 4))
    assert state.read_text().split() == ["1000", "7"]


def test_a_page_of_withheld_mail_is_stepped_over(monkeypatch, capsys):
    pages = [
        {"uidvalidity": 1000, "messages": [], "more": True, "scanned_to": 10, "last_uid": 20},
        {"uidvalidity": 1000, "messages": [message(15)], "more": False, "scanned_to": 20, "last_uid": 20},
    ]
    asked = []

    def ask(socket, request):
        asked.append(request["since_uid"])
        return {"ok": True, "result": pages[len(asked) - 1]}

    monkeypatch.setattr(cli, "ask", ask)
    assert watch.catch_up(Options(), (1000, 4)) == (1000, 20)
    assert asked == [4, 10]
    assert capsys.readouterr().out.split()[0] == "15"


def test_a_rebuilt_inbox_starts_over_from_its_newest(monkeypatch, capsys):
    serve(monkeypatch, [message(1)], last=1, uidvalidity=2000)
    mark = watch.resume(Options(), (1000, 40))
    assert mark == (2000, 1)
    assert "rebuilt" in capsys.readouterr().err


def test_a_rebuild_during_the_stream_starts_over(monkeypatch, capsys):
    serve(monkeypatch, [message(1)], pushed=[{"ok": True, "folder": "INBOX", "uidvalidity_changed": 2000}], last=1, uidvalidity=2000)
    mark, now = watch.follow(Options(), (1000, 40))
    assert mark == (2000, 1) and now


def test_a_refusal_in_the_stream_ends_this_round_and_waits(monkeypatch, capsys):
    serve(monkeypatch, [], pushed=[{"ok": False, "error": "mailbox work grants read on no address"}])
    _, now = watch.follow(Options(), (1000, 4))
    assert now is False
    assert "refused" in capsys.readouterr().out


def test_an_unreachable_service_is_waited_out(monkeypatch, capsys):
    def gone(socket, request):
        raise ConnectionRefusedError("no socket")

    monkeypatch.setattr(cli, "stream", gone)
    mark, now = watch.step(Options(), (1000, 4))
    assert mark == (1000, 4) and now is False


def test_once_prints_what_is_new_and_keeps_the_mark(monkeypatch, tmp_path, capsys):
    state = tmp_path / "state"
    state.write_text("1000 4\n")
    serve(monkeypatch, [message(4), message(5)])
    assert watch.main(["--mailbox", "work", "--once", "--state", str(state)]) == 0
    assert capsys.readouterr().out.splitlines() == ["5 boss@corp.com hi"]
    assert state.read_text().split() == ["1000", "5"]


def test_a_first_start_shows_nothing_old(monkeypatch, tmp_path, capsys):
    serve(monkeypatch, [message(4), message(5)])
    assert watch.main(["--mailbox", "work", "--once"]) == 0
    assert capsys.readouterr().out == ""
