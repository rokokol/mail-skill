"""Answering, forwarding and drafting decide on the addresses the message will reach.

A reply's recipients are computed here, not taken from the request, and the computed list
is what the grant is checked against: a Reply-To that points elsewhere is exactly the
case the check exists for. A reply comes from the own address the message reached, so an
alias is not traded for the mailbox behind it.
"""

from tests.conftest import mail
from tests.fake_imap import header

ANSWERED = b"\\Answered"
SEEN = b"\\Seen"


def test_a_reply_goes_to_reply_to_and_is_checked_there(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com", extra="Reply-To: collector@evil.org\r\n"))
    world.grant("work", "allow boss@corp.com: read, reply\n")
    answer = world.ask(action="reply", mailbox="work", uid=uid, text="ok")
    assert not answer["ok"]
    assert "collector@evil.org" in answer["error"]
    assert world.smtp.sent == []


def test_a_reply_needs_read_on_the_message_answered(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: reply\n")
    answer = world.ask(action="reply", mailbox="work", uid=uid, text="ok")
    assert not answer["ok"] and "read" in answer["remedy"]


def test_a_reply_threads_and_marks_the_original_answered(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com", subject="plan"))
    world.grant("work", "allow boss@corp.com: read, reply\n")
    answer = world.ask(action="reply", mailbox="work", uid=uid, text="ok")
    assert answer["ok"], answer
    raw = world.smtp.sent[0]["raw"]
    assert world.smtp.sent[0]["to"] == ["boss@corp.com"]
    assert header(raw, "In-Reply-To") == "<plan@x.org>"
    assert header(raw, "Subject") == "Re: plan"
    flags = server.folders["INBOX"].messages[uid]["flags"]
    assert ANSWERED in flags and SEEN not in flags


def test_a_reply_marks_read_only_when_the_file_says_so(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: read, reply\nmark-read-on-reply: yes\n")
    world.ask(action="reply", mailbox="work", uid=uid, text="ok")
    assert SEEN in server.folders["INBOX"].messages[uid]["flags"]


def test_a_reply_comes_from_the_alias_the_message_reached(world):
    server = world.mailbox("work", "work@example.org", own=[r"/.*@example\.org/"])
    uid = server.deliver(mail("bot@github.com", to="github@example.org"))
    world.grant("work", "allow bot@github.com: read, reply\n")
    answer = world.ask(action="reply", mailbox="work", uid=uid, text="ok")
    assert answer["ok"], answer
    assert world.smtp.sent[0]["from"] == "github@example.org"
    assert header(world.smtp.sent[0]["raw"], "From") == "github@example.org"


def test_reply_all_reaches_the_others_but_never_the_own_addresses(world):
    server = world.mailbox("work", "work@example.org", own=["github@example.org"])
    uid = server.deliver(
        mail("boss@corp.com", to="github@example.org, work@example.org", extra="Cc: alice@corp.com\r\n")
    )
    world.grant("work", "allow /.*@corp\\.com/: read, reply\n")
    answer = world.ask(action="reply", mailbox="work", uid=uid, text="ok", all=True)
    assert answer["ok"], answer
    assert world.smtp.sent[0]["to"] == ["boss@corp.com", "alice@corp.com"]


def test_a_folded_references_header_is_carried_on_one_line(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com", subject="t", extra="References: <a@x.org>\r\n <b@x.org>\r\n"))
    world.grant("work", "allow boss@corp.com: read, reply\n")
    assert world.ask(action="reply", mailbox="work", uid=uid, text="ok")["ok"]
    assert header(world.smtp.sent[0]["raw"], "References") == "<a@x.org> <b@x.org> <t@x.org>"


def test_a_forward_needs_read_on_the_source_and_forward_on_every_recipient(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com", subject="plan", body="the plan"))
    world.grant("work", "allow boss@corp.com: read\nallow colleague@corp.com: forward\n")
    answer = world.ask(action="forward", mailbox="work", uid=uid, to=["colleague@corp.com"])
    assert answer["ok"], answer
    raw = world.smtp.sent[0]["raw"]
    assert header(raw, "Subject") == "Fwd: plan"
    assert b"message/rfc822" in raw and b"the plan" in raw
    refused = world.ask(action="forward", mailbox="work", uid=uid, to=["stranger@x.org"])
    assert not refused["ok"] and "allow stranger@x.org:" in refused["remedy"]


def test_a_forward_of_an_unreadable_message_is_refused(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("stranger@x.org"))
    world.grant("work", "allow colleague@corp.com: forward\n")
    answer = world.ask(action="forward", mailbox="work", uid=uid, to=["colleague@corp.com"])
    assert not answer["ok"] and world.smtp.sent == []


def test_a_draft_is_filed_and_sent_nowhere(world):
    server = world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: draft\n")
    answer = world.ask(action="draft", mailbox="work", to=["boss@corp.com"], subject="later", text="t")
    assert answer["ok"], answer
    assert world.smtp.sent == []
    held = list(server.folders["Drafts"].messages.values())
    assert len(held) == 1 and b"\\Draft" in held[0]["flags"]


def test_a_draft_is_checked_like_a_send(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: draft\n")
    answer = world.ask(action="draft", mailbox="work", to=["boss@corp.com"], bcc=["stranger@x.org"], text="t")
    assert not answer["ok"] and "stranger@x.org" in answer["error"]


def test_a_draft_keeps_its_bcc_for_the_users_own_client(world):
    server = world.mailbox("work", "work@example.org")
    world.grant("work", "allow /.*@corp\\.com/: draft\n")
    world.ask(action="draft", mailbox="work", to=["a@corp.com"], bcc=["b@corp.com"], text="t")
    raw = list(server.folders["Drafts"].messages.values())[0]["raw"]
    assert header(raw, "Bcc") == "b@corp.com"


def test_send_never_stands_for_reply_forward_or_draft(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: read, send\n")
    assert not world.ask(action="reply", mailbox="work", uid=uid, text="x")["ok"]
    assert not world.ask(action="forward", mailbox="work", uid=uid, to=["boss@corp.com"])["ok"]
    assert not world.ask(action="draft", mailbox="work", to=["boss@corp.com"], text="x")["ok"]
