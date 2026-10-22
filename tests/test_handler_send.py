"""A send goes out only when every recipient is covered, and says nothing it should not.

Bcc lives in the envelope alone: neither the bytes on the wire nor the copy in Sent name
it. A header value with a line break would let the agent write a header of its own past
every check, so it is refused before anything is built.
"""

import base64

from tests.fake_imap import SEEN, header


def send(world, **fields):
    return world.ask(action="send", mailbox="work", **({"subject": "hi", "text": "hello"} | fields))


def test_a_send_with_one_uncovered_recipient_sends_nothing(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: send\n")
    answer = send(world, to=["boss@corp.com"], bcc=["stranger@x.org"])
    assert not answer["ok"]
    assert "allow stranger@x.org:" in answer["remedy"]
    assert world.smtp.sent == []


def test_bcc_is_in_the_envelope_and_nowhere_else(world):
    server = world.mailbox("work", "work@example.org")
    world.grant("work", "allow /.*@corp\\.com/: send\n")
    answer = send(world, to=["boss@corp.com"], bcc=["hidden@corp.com"])
    assert answer["ok"], answer
    wire = world.smtp.sent[0]
    assert set(wire["to"]) == {"boss@corp.com", "hidden@corp.com"}
    assert b"hidden@corp.com" not in wire["raw"]
    copy = list(server.folders["Sent"].messages.values())[0]
    assert b"hidden@corp.com" not in copy["raw"]
    assert SEEN in copy["flags"]


def test_the_message_comes_from_the_mailbox_with_its_own_message_id(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: send\n")
    answer = send(world, to=["boss@corp.com"])
    raw = world.smtp.sent[0]["raw"]
    assert world.smtp.sent[0]["from"] == "work@example.org"
    assert header(raw, "From") == "work@example.org"
    assert header(raw, "Message-ID") == answer["result"]["message_id"]
    assert answer["result"]["message_id"].endswith("@example.org>")


def test_a_line_break_in_a_header_value_is_refused(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: send\n")
    for fields in (
        {"subject": "hi\r\nBcc: stranger@x.org"},
        {"subject": "hi\nX: y"},
        {"subject": "hi\x00"},
    ):
        answer = send(world, to=["boss@corp.com"], **fields)
        assert not answer["ok"] and "line break" in answer["error"]
    assert world.smtp.sent == []


def test_a_recipient_must_be_one_bare_address(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow: send\n")
    for bad in ("boss@corp.com, stranger@x.org", "Boss <stranger@x.org>", "nobody", "a@x.org\r\nBcc: b@y.org"):
        answer = send(world, to=[bad])
        assert not answer["ok"], bad
    assert world.smtp.sent == []


def test_no_recipient_is_refused(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow: send\n")
    assert not send(world)["ok"]


def test_no_sent_copy_where_the_server_files_its_own(world):
    server = world.mailbox("work", "work@example.org", append_sent=False)
    world.grant("work", "allow boss@corp.com: send\n")
    assert send(world, to=["boss@corp.com"])["ok"]
    assert server.folders["Sent"].messages == {}


def test_a_missing_sent_folder_is_reported_after_the_send_not_instead_of_it(world):
    server = world.mailbox("work", "work@example.org")
    del server.folders["Sent"]
    world.grant("work", "allow boss@corp.com: send\n")
    answer = send(world, to=["boss@corp.com"])
    assert answer["ok"] and world.smtp.sent
    assert "Sent" in answer["result"]["warnings"][0]


def test_a_file_goes_out_as_an_attachment(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: send\n")
    data = base64.b64encode(b"%PDF-1").decode()
    answer = send(world, to=["boss@corp.com"], files=[{"name": "report.pdf", "data": data}])
    assert answer["ok"]
    raw = world.smtp.sent[0]["raw"]
    assert b"report.pdf" in raw and base64.b64encode(b"%PDF-1") in raw


def test_the_answer_names_every_recipient(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow /.*@corp\\.com/: send\n")
    result = send(world, to=["a@corp.com"], cc=["b@corp.com"], bcc=["c@corp.com"])["result"]
    assert result["to"] == ["a@corp.com"] and result["cc"] == ["b@corp.com"] and result["bcc"] == ["c@corp.com"]
