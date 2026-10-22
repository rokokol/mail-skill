"""The mailbox session reads without marking, names messages by UID and UIDVALIDITY,
finds special folders by their flag, and never expunges more than it was asked to.
"""

import pytest

from mail_agentd import client
from tests.fake_imap import DELETED, SEEN, FakeServer, FakeSMTP

RAW = b"From: a@x.org\r\nTo: work@example.org\r\nSubject: hi\r\n\r\nbody\r\n"


@pytest.fixture
def world():
    server = FakeServer()
    server.folder("Sent", [b"\\Sent"])
    server.folder("Drafts", [b"\\Drafts"])
    server.folder("Bin", [b"\\Trash"])
    smtp = FakeSMTP()
    config = client.MailboxConfig(
        name="work",
        address="work@example.org",
        user="work@example.org",
        imap_host="mail",
        smtp_host="mail",
    )
    box = client.Mailbox(config, "secret", "secret", server.connect, smtp.connect)
    return server, smtp, box


def test_reading_headers_and_body_leaves_the_message_unseen(world):
    server, _, box = world
    uid = server.deliver(RAW)
    box.headers("INBOX", [uid])
    assert box.body("INBOX", uid) == RAW
    assert SEEN not in server.folders["INBOX"].messages[uid]["flags"]


def test_uids_after_a_mark_exclude_the_mark_itself(world):
    server, _, box = world
    first = server.deliver(RAW)
    # UID n:* answers with the highest message when n is above it
    assert box.uids("INBOX", after=first) == []
    second = server.deliver(RAW)
    assert box.uids("INBOX", after=first) == [second]


def test_a_rebuilt_folder_is_refused_by_its_uidvalidity(world):
    server, _, box = world
    uid = server.deliver(RAW)
    with pytest.raises(client.Moved):
        box.body("INBOX", uid, uidvalidity=1)
    assert box.uidvalidity("INBOX") == server.folders["INBOX"].uidvalidity


def test_a_special_folder_is_found_by_its_flag(world):
    _, _, box = world
    assert box.special("trash") == "Bin"
    assert box.special("sent") == "Sent"


def test_a_configured_folder_wins_over_the_flag(world):
    server, smtp, _ = world
    server.folder("Papierkorb")
    config = client.MailboxConfig(
        name="work",
        address="work@example.org",
        user="work@example.org",
        imap_host="mail",
        smtp_host="mail",
        folders={"trash": "Papierkorb"},
    )
    box = client.Mailbox(config, "secret", "secret", server.connect, smtp.connect)
    assert box.special("trash") == "Papierkorb"


def test_a_folder_no_flag_names_is_refused_rather_than_guessed(world):
    server, smtp, box = world
    del server.folders["Bin"]
    server.folder("Trash")
    with pytest.raises(client.NoFolder, match="trash"):
        box.special("trash")


def test_deleting_moves_to_trash_and_keeps_the_message(world):
    server, _, box = world
    uid = server.deliver(RAW)
    box.trash("INBOX", [uid])
    assert uid not in server.folders["INBOX"].messages
    assert len(server.folders["Bin"].messages) == 1


def test_a_move_without_the_move_extension_copies_then_removes_only_those(world):
    server, _, box = world
    server.capabilities.discard("MOVE")
    keep = server.deliver(RAW)
    server.folders["INBOX"].messages[keep]["flags"].add(DELETED)
    uid = server.deliver(RAW)
    box.move("INBOX", [uid], "Sent")
    assert uid not in server.folders["INBOX"].messages
    # Another client's message marked deleted is not this request's to remove
    assert keep in server.folders["INBOX"].messages
    assert ("expunge", (None,)) not in server.calls


def test_expunging_needs_uidplus_and_never_falls_back(world):
    server, _, box = world
    server.capabilities.discard("UIDPLUS")
    uid = server.deliver(RAW)
    with pytest.raises(client.Unsupported, match="UIDPLUS"):
        box.expunge("INBOX", [uid])
    assert uid in server.folders["INBOX"].messages
    assert DELETED not in server.folders["INBOX"].messages[uid]["flags"]


def test_expunging_removes_the_named_message(world):
    server, _, box = world
    uid = server.deliver(RAW)
    box.expunge("INBOX", [uid])
    assert uid not in server.folders["INBOX"].messages


def test_marking_seen_and_unseen(world):
    server, _, box = world
    uid = server.deliver(RAW)
    box.set_seen("INBOX", [uid], True)
    assert SEEN in server.folders["INBOX"].messages[uid]["flags"]
    box.set_seen("INBOX", [uid], False)
    assert SEEN not in server.folders["INBOX"].messages[uid]["flags"]


def test_a_lost_connection_is_opened_again_once(world):
    server, _, box = world
    uid = server.deliver(RAW)
    box.headers("INBOX", [uid])
    server.fail_next.append("fetch")
    assert box.body("INBOX", uid) == RAW
    assert len(server.sessions) == 2


def test_a_wrong_password_is_its_own_error(world):
    server, smtp, _ = world
    config = client.MailboxConfig(
        name="work", address="w@r.art", user="w@r.art", imap_host="mail", smtp_host="mail"
    )
    box = client.Mailbox(config, "wrong", "secret", server.connect, smtp.connect)
    with pytest.raises(client.AuthFailed):
        box.folders()


def test_folders_carry_their_counts(world):
    server, _, box = world
    server.deliver(RAW)
    inbox = next(f for f in box.folders() if f["name"] == "INBOX")
    assert inbox["messages"] == 1 and inbox["unseen"] == 1


def test_sending_hands_the_envelope_apart_from_the_bytes(world):
    _, smtp, box = world
    box.send("work@example.org", ["a@x.org", "hidden@x.org"], RAW)
    assert smtp.sent == [{"from": "work@example.org", "to": ["a@x.org", "hidden@x.org"], "raw": RAW}]


def test_appending_files_a_copy_with_its_flags(world):
    server, _, box = world
    box.append("Sent", RAW, [SEEN])
    held = list(server.folders["Sent"].messages.values())
    assert held[0]["raw"] == RAW and SEEN in held[0]["flags"]
