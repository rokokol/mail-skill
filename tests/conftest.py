"""A service assembled around stand-ins: one IMAP server per mailbox, one SMTP sink."""

import asyncio

import pytest

from mail_agentd import client, handler, hub, media, permissions
from tests.fake_imap import FakeServer, FakeSMTP


class World:
    def __init__(self, root):
        (root / "permissions" / "mailboxes").mkdir(parents=True)
        self.root = root
        self.servers = {}
        self.smtp = FakeSMTP()
        self.boxes = {}
        self.store = permissions.Store(root / "permissions")
        self.outbox = media.Outbox(root / "outbox")
        self.hub = hub.Hub()
        self.watchers = {}
        self.handler = handler.Handler(
            self.store, self.boxes, outbox=self.outbox, hub=self.hub, watchers=self.watchers
        )

    def mailbox(self, name, address, own=(), **fields):
        server = FakeServer()
        server.folder("Sent", [b"\\Sent"])
        server.folder("Drafts", [b"\\Drafts"])
        server.folder("Trash", [b"\\Trash"])
        self.servers[name] = server
        config = client.MailboxConfig(
            name=name,
            address=address,
            user=address,
            imap_host="mail",
            smtp_host="mail",
            own=list(own),
            **fields,
        )
        self.boxes[name] = client.Mailbox(config, "secret", "secret", server.connect, self.smtp.connect)
        return server

    def grant(self, name, text):
        (self.root / "permissions" / "mailboxes" / f"{name}.conf").write_text(text)

    def ask(self, **request):
        return asyncio.run(self.handler.handle(request))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def mail(sender="a@x.org", to="work@example.org", subject="hi", body="body", extra=""):
    return (
        f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n"
        f"Message-ID: <{subject.replace(' ', '-')}@x.org>\r\n{extra}\r\n{body}\r\n"
    ).encode()
