"""One mailbox's IMAP session and its SMTP submission, with what each command assumes made explicit.

IMAP leaves several choices to the client, and the default of each is the wrong one here.
A plain fetch of the body marks the message seen, so every read peeks. A message number
moves when another client deletes, so every message is named by UID within a UIDVALIDITY.
A plain EXPUNGE removes every message any client marked deleted, so removal goes through
UID EXPUNGE alone. A folder found by a guessed name may be the wrong one, so special
folders come from their RFC 6154 flag or from the configuration.

Everything here blocks. The service runs it in a worker thread, one command at a time per
session.
"""

import imaplib
from dataclasses import dataclass, field

from imapclient.exceptions import CapabilityError, IMAPClientAbortError, LoginError

SEEN = b"\\Seen"
DELETED = b"\\Deleted"

# The RFC 6154 flag that names each special folder a request can reach
SPECIAL = {
    "sent": b"\\Sent",
    "drafts": b"\\Drafts",
    "trash": b"\\Trash",
}

# A dropped connection shows up as any of these, from the socket up to imapclient
LOST = (OSError, imaplib.IMAP4.abort, IMAPClientAbortError)


class Moved(LookupError):
    """The folder was rebuilt since the identifier was taken, so its UIDs name other mail."""


class NoFolder(LookupError):
    """No folder carries the special-use flag, and the configuration names none."""


class Unsupported(RuntimeError):
    """The server lacks an extension this command needs, and no safe substitute exists."""


class AuthFailed(PermissionError):
    """The server refused the password. Retrying cannot fix that."""


@dataclass
class MailboxConfig:
    """What the service knows about a mailbox, none of it secret."""

    name: str
    address: str
    user: str
    imap_host: str
    smtp_host: str
    imap_port: int = 993
    smtp_port: int = 465
    folders: dict = field(default_factory=dict)
    append_sent: bool = True
    own: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, name, fields):
        return cls(name=name, **fields)


class Mailbox:
    """A lazily opened session, opened again once when the connection drops."""

    def __init__(self, config, imap_password, smtp_password, imap_connect, smtp_connect):
        self.config = config
        self._imap_password = imap_password
        self._smtp_password = smtp_password
        self._imap_connect = imap_connect
        self._smtp_connect = smtp_connect
        self._session = None

    def open_session(self):
        """A logged-in session of its own, for a caller that holds it outside the lock."""
        return self._open()

    def _open(self):
        session = self._imap_connect(self.config.imap_host, self.config.imap_port)
        try:
            session.login(self.config.user, self._imap_password)
        except LoginError as refused:
            raise AuthFailed(f"mailbox {self.config.name}: {refused}") from refused
        return session

    def _run(self, command):
        """Run one command on the session, opening a new one once if the old one died."""
        for attempt in (1, 2):
            if self._session is None:
                self._session = self._open()
            try:
                return command(self._session)
            except LOST:
                self._session = None
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def close(self):
        if self._session is not None:
            try:
                self._session.logout()
            except LOST:
                pass
            self._session = None

    def _select(self, session, folder, uidvalidity=None):
        status = session.select_folder(folder)
        current = status[b"UIDVALIDITY"]
        if uidvalidity is not None and int(uidvalidity) != current:
            raise Moved(
                f"folder {folder} was rebuilt: its UIDVALIDITY is {current}, not {uidvalidity}, "
                f"so its identifiers name other messages now"
            )
        return current

    def folders(self):
        def command(session):
            listed = []
            for flags, _, name in session.list_folders():
                if b"\\Noselect" in flags:
                    continue
                status = session.folder_status(name, ["MESSAGES", "UNSEEN", "UIDVALIDITY"])
                listed.append(
                    {
                        "name": name,
                        "special": [f.decode().lstrip("\\").lower() for f in flags if f in SPECIAL.values()],
                        "messages": status[b"MESSAGES"],
                        "unseen": status[b"UNSEEN"],
                        "uidvalidity": status[b"UIDVALIDITY"],
                    }
                )
            return listed

        return self._run(command)

    def uidvalidity(self, folder):
        return self._run(lambda session: self._select(session, folder))

    def uids(self, folder, *, after=None, criteria=None, uidvalidity=None):
        """The UIDs in a folder, oldest first, above `after` when it is given.

        UID n:* names the highest message even when n is above it, so the range alone
        would hand back a message already seen. The answer is filtered here.
        """

        def command(session):
            self._select(session, folder, uidvalidity)
            search = list(criteria or [])
            if after is not None:
                search = ["UID", f"{int(after) + 1}:*", *search]
            found = session.search(search or ["ALL"])
            return sorted(u for u in found if after is None or u > int(after))

        return self._run(command)

    def headers(self, folder, uids, *, uidvalidity=None):
        """The header block and flags of each UID that still exists."""

        def command(session):
            self._select(session, folder, uidvalidity)
            if not uids:
                return {}
            fetched = session.fetch(list(uids), ["BODY.PEEK[HEADER]", "FLAGS", "RFC822.SIZE"])
            return {
                uid: {
                    "header": data[b"BODY[HEADER]"],
                    "flags": [f.decode() for f in data.get(b"FLAGS", ())],
                    "size": data.get(b"RFC822.SIZE"),
                }
                for uid, data in fetched.items()
            }

        return self._run(command)

    def body(self, folder, uid, *, uidvalidity=None):
        """The whole message, fetched without marking it seen. None when it is gone."""

        def command(session):
            self._select(session, folder, uidvalidity)
            fetched = session.fetch([uid], ["BODY.PEEK[]"])
            return fetched.get(uid, {}).get(b"BODY[]")

        return self._run(command)

    def set_seen(self, folder, uids, seen, *, uidvalidity=None):
        def command(session):
            self._select(session, folder, uidvalidity)
            (session.add_flags if seen else session.remove_flags)(list(uids), [SEEN])

        self._run(command)

    def set_flag(self, folder, uids, flag, *, uidvalidity=None):
        def command(session):
            self._select(session, folder, uidvalidity)
            session.add_flags(list(uids), [flag])

        self._run(command)

    def special(self, kind):
        """The folder for sent mail, drafts or trash: configured, else flagged."""
        configured = self.config.folders.get(kind)
        if configured:
            return configured
        flag = SPECIAL[kind]

        def command(session):
            for flags, _, name in session.list_folders():
                if flag in flags:
                    return name
            return None

        found = self._run(command)
        if found is None:
            raise NoFolder(
                f"mailbox {self.config.name}: no folder carries {flag.decode()}, and "
                f"folders.{kind} is not configured"
            )
        return found

    def _remove(self, session, uids):
        """Remove exactly these UIDs from the selected folder, or refuse."""
        if not session.has_capability("UIDPLUS"):
            raise Unsupported(
                f"mailbox {self.config.name}: the server lacks UIDPLUS, and a plain EXPUNGE "
                f"would also remove what other clients marked deleted"
            )
        session.add_flags(list(uids), [DELETED])
        session.uid_expunge(list(uids))

    def move(self, folder, uids, target, *, uidvalidity=None):
        def command(session):
            self._select(session, folder, uidvalidity)
            if session.has_capability("MOVE"):
                try:
                    session.move(list(uids), target)
                    return
                except CapabilityError:
                    pass
            if not session.has_capability("UIDPLUS"):
                raise Unsupported(
                    f"mailbox {self.config.name}: the server has neither MOVE nor UIDPLUS, "
                    f"so a move cannot remove the original alone"
                )
            session.copy(list(uids), target)
            self._remove(session, uids)

        self._run(command)

    def trash(self, folder, uids, *, uidvalidity=None):
        self.move(folder, uids, self.special("trash"), uidvalidity=uidvalidity)

    def expunge(self, folder, uids, *, uidvalidity=None):
        def command(session):
            self._select(session, folder, uidvalidity)
            self._remove(session, uids)

        self._run(command)

    def append(self, folder, raw, flags=()):
        self._run(lambda session: session.append(folder, raw, list(flags)))

    def send(self, envelope_from, recipients, raw):
        """Submit the bytes to these envelope recipients. Bcc lives only in the envelope."""
        with self._smtp_connect(self.config.smtp_host, self.config.smtp_port) as smtp:
            smtp.login(self.config.user, self._smtp_password)
            smtp.sendmail(envelope_from, list(recipients), raw)
