"""A stand-in for an IMAP server and imapclient's session, with the server's own quirks.

Two quirks are modelled because the code has to survive them on a real server. A fetch of
BODY[] without PEEK sets the seen flag, so a read that forgets PEEK shows up in the state.
UID n:* answers with the highest message even when n is above it, so a watcher that
trusts the range shows the same message again.
"""

import itertools
import re
import smtplib

from imapclient.exceptions import CapabilityError, LoginError

SEEN = b"\\Seen"
DELETED = b"\\Deleted"


class Folder:
    def __init__(self, name, special=(), uidvalidity=1000):
        self.name = name
        self.special = tuple(special)
        self.uidvalidity = uidvalidity
        self.uidnext = 1
        self.messages = {}

    def add(self, raw, flags=()):
        uid = self.uidnext
        self.uidnext += 1
        self.messages[uid] = {"raw": raw, "flags": set(flags)}
        return uid


class FakeServer:
    """The state every session of one account shares."""

    def __init__(self, capabilities=("IMAP4REV1", "IDLE", "MOVE", "UIDPLUS", "SPECIAL-USE")):
        self.capabilities = {c.upper() for c in capabilities}
        self.folders = {"INBOX": Folder("INBOX")}
        self.password = "secret"
        self.calls = []
        self.sessions = []
        self.idle_queue = []
        self.fail_next = []

    def folder(self, name, special=()):
        self.folders[name] = Folder(name, special)
        return self.folders[name]

    def deliver(self, raw, folder="INBOX", flags=()):
        uid = self.folders[folder].add(raw, flags)
        self.idle_queue.append((len(self.folders[folder].messages), b"EXISTS"))
        return uid

    def connect(self, host, port):
        session = FakeIMAP(self, host, port)
        self.sessions.append(session)
        return session


def _uid_set(text, existing):
    """The UIDs an IMAP set names among the existing ones, with the n:* quirk."""
    chosen = set()
    highest = max(existing, default=0)
    for part in text.split(","):
        if ":" in part:
            low, high = part.split(":")
            low = int(low)
            high = highest if high == "*" else int(high)
            if low > high:
                low, high = high, low
            chosen.update(u for u in existing if low <= u <= high)
        else:
            chosen.add(int(part))
    return chosen & set(existing)


class FakeIMAP:
    def __init__(self, server, host, port):
        self.server = server
        self.host = host
        self.port = port
        self.logged_in = False
        self.selected = None
        self.idling = False

    def _call(self, name, *args):
        self.server.calls.append((name, args))
        if self.server.fail_next and self.server.fail_next[0] == name:
            self.server.fail_next.pop(0)
            raise OSError(f"connection lost during {name}")

    def _folder(self):
        if self.selected is None:
            raise RuntimeError("no folder selected")
        return self.server.folders[self.selected]

    def login(self, username, password):
        self._call("login", username)
        if password != self.server.password:
            raise LoginError("[AUTHENTICATIONFAILED] Authentication failed.")
        self.logged_in = True

    def logout(self):
        self._call("logout")
        self.logged_in = False

    def capabilities(self):
        return tuple(c.encode() for c in sorted(self.server.capabilities))

    def has_capability(self, capability):
        return capability.upper() in self.server.capabilities

    def list_folders(self, directory="", pattern="*"):
        self._call("list_folders")
        return [(f.special, b"/", f.name) for f in self.server.folders.values()]

    def folder_status(self, folder, what=None):
        self._call("folder_status", folder)
        f = self.server.folders[folder]
        return {
            b"MESSAGES": len(f.messages),
            b"UNSEEN": sum(1 for m in f.messages.values() if SEEN not in m["flags"]),
            b"UIDVALIDITY": f.uidvalidity,
            b"UIDNEXT": f.uidnext,
        }

    def select_folder(self, folder, readonly=False):
        self._call("select_folder", folder, readonly)
        if folder not in self.server.folders:
            raise KeyError(folder)
        self.selected = folder
        f = self.server.folders[folder]
        return {b"UIDVALIDITY": f.uidvalidity, b"UIDNEXT": f.uidnext, b"EXISTS": len(f.messages)}

    def search(self, criteria="ALL", charset=None):
        self._call("search", criteria)
        f = self._folder()
        terms = [criteria] if isinstance(criteria, str) else list(criteria)
        uids = set(f.messages)
        index = 0
        while index < len(terms):
            term = str(terms[index]).upper()
            if term == "ALL":
                pass
            elif term == "UNSEEN":
                uids = {u for u in uids if SEEN not in f.messages[u]["flags"]}
            elif term == "UID":
                index += 1
                uids &= _uid_set(str(terms[index]), f.messages)
            elif term in ("TEXT", "FROM", "SUBJECT", "TO"):
                index += 1
                needle = str(terms[index]).lower().encode()
                uids = {u for u in uids if needle in f.messages[u]["raw"].lower()}
            else:
                raise ValueError(f"the fake does not search {term}")
            index += 1
        return sorted(uids)

    def fetch(self, messages, data, modifiers=None):
        self._call("fetch", tuple(messages), tuple(data))
        f = self._folder()
        answer = {}
        for uid in messages:
            if uid not in f.messages:
                continue
            held = f.messages[uid]
            raw = held["raw"]
            item = {}
            for wanted in data:
                key = wanted.upper()
                if key == "FLAGS":
                    item[b"FLAGS"] = tuple(sorted(held["flags"]))
                elif key == "RFC822.SIZE":
                    item[b"RFC822.SIZE"] = len(raw)
                elif key == "BODY.PEEK[HEADER]":
                    item[b"BODY[HEADER]"] = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                elif key == "BODY.PEEK[]":
                    item[b"BODY[]"] = raw
                elif key in ("BODY[]", "RFC822"):
                    item[b"BODY[]"] = raw
                    held["flags"].add(SEEN)
                else:
                    raise ValueError(f"the fake does not fetch {wanted}")
            answer[uid] = item
        return answer

    def add_flags(self, messages, flags, silent=False):
        self._call("add_flags", tuple(messages), tuple(flags))
        for uid in messages:
            self._folder().messages[uid]["flags"].update(flags)

    def remove_flags(self, messages, flags, silent=False):
        self._call("remove_flags", tuple(messages), tuple(flags))
        for uid in messages:
            self._folder().messages[uid]["flags"].difference_update(flags)

    def copy(self, messages, folder):
        self._call("copy", tuple(messages), folder)
        target = self.server.folders[folder]
        for uid in messages:
            held = self._folder().messages[uid]
            target.add(held["raw"], held["flags"])

    def move(self, messages, folder):
        if "MOVE" not in self.server.capabilities:
            raise CapabilityError("Server does not support MOVE capability")
        self._call("move", tuple(messages), folder)
        target = self.server.folders[folder]
        for uid in messages:
            held = self._folder().messages.pop(uid)
            target.add(held["raw"], held["flags"])

    def uid_expunge(self, messages):
        if "UIDPLUS" not in self.server.capabilities:
            raise CapabilityError("Server does not support UIDPLUS capability")
        self._call("uid_expunge", tuple(messages))
        f = self._folder()
        for uid in messages:
            if uid in f.messages and DELETED in f.messages[uid]["flags"]:
                del f.messages[uid]

    def expunge(self, messages=None):
        self._call("expunge", messages)
        f = self._folder()
        for uid in [u for u, m in f.messages.items() if DELETED in m["flags"]]:
            del f.messages[uid]

    def append(self, folder, msg, flags=(), msg_time=None):
        self._call("append", folder, tuple(flags))
        raw = msg if isinstance(msg, bytes) else msg.encode()
        self.server.folders[folder].add(raw, flags)

    def noop(self):
        self._call("noop")
        return (b"NOOP completed", [])

    def idle(self):
        self._call("idle")
        self.idling = True

    def idle_check(self, timeout=None):
        self._call("idle_check", timeout)
        responses, self.server.idle_queue = self.server.idle_queue, []
        return responses

    def idle_done(self):
        self._call("idle_done")
        self.idling = False
        return (b"IDLE terminated", [])


class FakeSMTP:
    """Records what would have gone over the wire, envelope apart from the bytes."""

    def __init__(self):
        self.sent = []
        self.password = "secret"
        self.connections = itertools.count()

    def connect(self, host, port):
        next(self.connections)
        return _SMTPSession(self)


class _SMTPSession:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if password != self.owner.password:
            raise smtplib.SMTPAuthenticationError(535, b"authentication failed")

    def sendmail(self, from_addr, to_addrs, msg):
        self.owner.sent.append(
            {"from": from_addr, "to": list(to_addrs), "raw": msg if isinstance(msg, bytes) else msg.encode()}
        )
        return {}


def header(raw, name):
    """One header's raw value from message bytes, for assertions."""
    match = re.search(rb"^" + name.encode() + rb":\s*(.*)$", raw, re.M | re.I)
    return match.group(1).decode().strip() if match else None
