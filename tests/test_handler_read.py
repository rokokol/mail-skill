"""Reading answers only with what the grant reaches, and says how much it held back.

A message out of reach is not described at all: not its sender, not its subject. Only the
count of what was held back is given, so a summary is never mistaken for the whole folder.
"""

import base64
import email.policy
from email.message import EmailMessage

from tests.conftest import mail
from tests.fake_imap import SEEN


def test_permissions_and_mailboxes_need_no_grant(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: read\n")
    held = world.ask(action="permissions")
    assert held["ok"] and "work" in held["result"]["mailboxes"]
    boxes = world.ask(action="mailboxes")
    assert boxes["result"] == [{"name": "work", "address": "work@example.org"}]


def test_an_unknown_mailbox_is_refused_by_name(world):
    answer = world.ask(action="read", mailbox="nope")
    assert not answer["ok"] and "nope" in answer["error"]


def test_a_read_shows_only_messages_in_reach_and_counts_the_rest(world):
    server = world.mailbox("work", "work@example.org")
    server.deliver(mail("boss@corp.com", subject="granted"))
    server.deliver(mail("stranger@x.org", subject="secret plan"))
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="read", mailbox="work")
    assert answer["ok"]
    subjects = [m["subject"] for m in answer["result"]["messages"]]
    assert subjects == ["granted"]
    assert answer["result"]["withheld"] == 1
    assert "secret plan" not in str(answer)


def test_a_read_marks_nothing_seen(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: read\n")
    world.ask(action="read", mailbox="work")
    world.ask(action="read", mailbox="work", uid=uid)
    assert SEEN not in server.folders["INBOX"].messages[uid]["flags"]


def test_one_message_is_read_whole_under_its_grant(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com", body="the whole text"))
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="read", mailbox="work", uid=uid)
    assert answer["ok"] and "the whole text" in answer["result"]["text"]
    assert answer["allowed_by"] == "read"
    assert answer["result"]["uidvalidity"] == server.folders["INBOX"].uidvalidity


def test_one_message_out_of_reach_is_refused_with_the_line_that_lifts_it(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("stranger@x.org", body="private"))
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="read", mailbox="work", uid=uid)
    assert not answer["ok"]
    assert "allow stranger@x.org:" in answer["remedy"]
    assert "private" not in str(answer)


def test_the_own_address_in_to_is_not_a_second_party(world):
    server = world.mailbox("work", "work@example.org", own=[r"/.*@example\.org/"])
    server.deliver(mail("stranger@x.org", to="github@example.org"))
    world.grant("work", r"allow /.*@example\.org/: read" + "\n")
    assert world.ask(action="read", mailbox="work")["result"]["messages"] == []


def test_a_to_grant_reads_what_reached_that_own_address(world):
    server = world.mailbox("work", "work@example.org", own=["github@example.org"])
    server.deliver(mail("notifications@github.com", to="github@example.org", subject="ci"))
    server.deliver(mail("someone@x.org", to="work@example.org", subject="other"))
    world.grant("work", "allow to github@example.org: read\n")
    answer = world.ask(action="read", mailbox="work")
    assert [m["subject"] for m in answer["result"]["messages"]] == ["ci"]
    assert answer["result"]["messages"][0]["via"] == "allow to github@example.org"


def test_the_mailbox_address_is_always_its_own(world):
    server = world.mailbox("work", "work@example.org")
    server.deliver(mail("stranger@x.org", to="work@example.org"))
    world.grant("work", "allow work@example.org: read\n")
    assert world.ask(action="read", mailbox="work")["result"]["messages"] == []


def test_reading_since_a_uid_returns_only_newer_mail(world):
    server = world.mailbox("work", "work@example.org")
    first = server.deliver(mail("boss@corp.com", subject="one"))
    server.deliver(mail("boss@corp.com", subject="two"))
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="read", mailbox="work", since_uid=first)
    assert [m["subject"] for m in answer["result"]["messages"]] == ["two"]


def test_a_listing_names_the_highest_uid_of_the_folder_and_nothing_of_it(world):
    server = world.mailbox("work", "work@example.org")
    server.deliver(mail("boss@corp.com", subject="granted"))
    last = server.deliver(mail("stranger@x.org", subject="secret plan"))
    world.grant("work", "allow boss@corp.com: read\n")
    result = world.ask(action="read", mailbox="work", limit=1)["result"]
    assert result["last_uid"] == last
    assert [m["subject"] for m in result["messages"]] == ["granted"]


def test_a_catch_up_says_where_it_stopped_past_withheld_mail(world, monkeypatch):
    from mail_agentd import handler

    monkeypatch.setattr(handler, "SCAN", 2)
    server = world.mailbox("work", "work@example.org")
    server.deliver(mail("stranger@x.org"))
    second = server.deliver(mail("stranger@x.org"))
    server.deliver(mail("boss@corp.com", subject="later"))
    world.grant("work", "allow boss@corp.com: read\n")
    result = world.ask(action="read", mailbox="work", since_uid=0)["result"]
    assert result["messages"] == [] and result["more"] is True
    assert result["scanned_to"] == second


def test_a_limit_keeps_the_newest(world):
    server = world.mailbox("work", "work@example.org")
    for n in range(5):
        server.deliver(mail("boss@corp.com", subject=f"m{n}"))
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="read", mailbox="work", limit=2)
    assert [m["subject"] for m in answer["result"]["messages"]] == ["m3", "m4"]


def test_listing_folders_needs_the_list_word(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: all\n")
    assert not world.ask(action="list", mailbox="work")["ok"]
    world.grant("work", "allow: list\n")
    names = [f["name"] for f in world.ask(action="list", mailbox="work")["result"]]
    assert "INBOX" in names and "Trash" in names


def test_a_wildcard_grant_is_reported_as_one(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: all\n")
    answer = world.ask(action="read", mailbox="work", uid=uid)
    assert answer["allowed_by"] == "all" and answer["wildcard"]


def test_a_rebuilt_folder_is_refused(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="read", mailbox="work", uid=uid, uidvalidity=1)
    assert not answer["ok"] and "rebuilt" in answer["error"]


def _with_attachment():
    msg = EmailMessage()
    msg["From"] = "boss@corp.com"
    msg["To"] = "work@example.org"
    msg["Subject"] = "report"
    msg.set_content("see attached")
    msg.add_attachment(b"%PDF-data", maintype="application", subtype="pdf", filename="../../report.pdf")
    return msg.as_bytes(policy=email.policy.SMTP)


def test_media_lands_in_the_outbox_under_a_safe_name(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(_with_attachment())
    world.grant("work", "allow boss@corp.com: read, media\n")
    answer = world.ask(action="media", mailbox="work", uid=uid)
    assert answer["ok"]
    saved = answer["result"]["files"]
    assert len(saved) == 1
    path = saved[0]["path"]
    assert path.startswith(str(world.root / "outbox"))
    assert open(path, "rb").read() == b"%PDF-data"


def test_media_needs_its_own_word(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(_with_attachment())
    world.grant("work", "allow boss@corp.com: read\n")
    answer = world.ask(action="media", mailbox="work", uid=uid)
    assert not answer["ok"] and "media" in answer["remedy"]


def test_a_broken_permission_file_is_an_answer_not_a_crash(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: raed\n")
    answer = world.ask(action="read", mailbox="work")
    assert not answer["ok"] and "raed" in answer["error"]


def test_an_unknown_action_is_refused(world):
    world.mailbox("work", "work@example.org")
    answer = world.ask(action="explode", mailbox="work")
    assert not answer["ok"] and "explode" in answer["error"]


def test_an_encoded_body_reads_as_text(world):
    server = world.mailbox("work", "work@example.org")
    encoded = base64.b64encode("Привет".encode()).decode()
    uid = server.deliver(
        mail(
            "boss@corp.com",
            body=encoded,
            extra="MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n",
        )
    )
    world.grant("work", "allow boss@corp.com: read\n")
    assert "Привет" in world.ask(action="read", mailbox="work", uid=uid)["result"]["text"]
