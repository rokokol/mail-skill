"""The watcher holds IDLE on INBOX in a session of its own and publishes what arrives.

It publishes header blocks, not decisions: who may see a message is decided per
subscriber, at the moment of delivery. A server without IDLE gets no polling substitute;
the watcher says so once, and a subscription is refused with that reason.
"""

from mail_agentd import client, watch
from tests.fake_imap import FakeServer, FakeSMTP

RAW = b"From: a@x.org\r\nTo: work@example.org\r\nSubject: hi\r\nMessage-ID: <1@x.org>\r\n\r\nbody\r\n"


def make(capabilities=None):
    server = FakeServer() if capabilities is None else FakeServer(capabilities)
    config = client.MailboxConfig(
        name="work", address="work@example.org", user="work@example.org", imap_host="mail", smtp_host="mail"
    )
    box = client.Mailbox(config, "secret", "secret", server.connect, FakeSMTP().connect)
    published = []
    watcher = watch.Watcher(box, lambda key, event: published.append((key, event)), idle_seconds=7)
    return server, watcher, published


def test_a_new_message_is_published_with_its_header_block():
    server, watcher, published = make()
    server.deliver(RAW)
    watcher.connect()
    assert published == []
    uid = server.deliver(RAW)
    watcher.step()
    assert [(k, e["uid"]) for k, e in published] == [("work", uid)]
    event = published[0][1]
    assert event["folder"] == "INBOX" and event["header"].startswith(b"From: a@x.org")
    assert event["uidvalidity"] == server.folders["INBOX"].uidvalidity


def test_a_quiet_round_publishes_nothing_again():
    server, watcher, published = make()
    server.deliver(RAW)
    watcher.connect()
    watcher.step()
    watcher.step()
    assert published == []


def test_idle_is_renewed_within_the_servers_timeout():
    server, watcher, _ = make()
    watcher.connect()
    watcher.step()
    names = [name for name, _ in server.calls]
    assert names.index("idle") < names.index("idle_check") < names.index("idle_done")
    assert ("idle_check", (7,)) in server.calls


def test_a_rebuilt_inbox_is_announced_and_followed_from_its_newest():
    server, watcher, published = make()
    watcher.connect()
    server.folders["INBOX"].uidvalidity = 2000
    server.deliver(RAW)
    watcher.step()
    assert published[0][1] == {"folder": "INBOX", "uidvalidity_changed": 2000}
    uid = server.deliver(RAW)
    watcher.step()
    assert published[-1][1]["uid"] == uid


def test_a_server_without_idle_is_named_and_not_polled():
    server, watcher, published = make(capabilities=("IMAP4REV1", "UIDPLUS"))
    watcher.connect()
    assert "IDLE" in watcher.unsupported
    assert "idle" not in [name for name, _ in server.calls]


def test_a_lost_connection_is_opened_again_and_misses_nothing():
    server, watcher, published = make()
    watcher.connect()
    server.fail_next.append("idle_check")
    uid = server.deliver(RAW)
    watcher.run(rounds=2, retry_seconds=0)
    assert [e["uid"] for _, e in published] == [uid]
    assert len(server.sessions) == 2
