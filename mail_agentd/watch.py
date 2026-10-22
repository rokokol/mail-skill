"""Hold IDLE on a mailbox's INBOX and publish each message that arrives.

The watcher keeps a session of its own. IDLE occupies a session until it is ended, and the
session that serves requests cannot wait on the server and answer the agent at once.

It publishes header blocks, never decisions. Who may see a message is decided for each
subscriber when the message reaches them, under the permission file as it is then.

A server without IDLE gets no polling in its place. The watcher names the gap once, and
a subscription to that mailbox is refused with the reason, so nobody waits on a watch
that is not happening.
"""

import threading

from . import client

FOLDER = "INBOX"

# RFC 2177 lets a server end IDLE after thirty minutes, so it is renewed a little sooner
IDLE_SECONDS = 29 * 60
RETRY_SECONDS = 30


class Watcher:
    """One mailbox's IDLE loop, run in a thread of its own."""

    def __init__(self, box, publish, *, idle_seconds=IDLE_SECONDS):
        self._box = box
        self._publish = publish
        self._idle_seconds = idle_seconds
        self._session = None
        self._mark = None
        self._uidvalidity = None
        self.unsupported = None
        self.stopped = threading.Event()

    @property
    def name(self):
        return self._box.config.name

    def connect(self):
        """Open the session and take the newest message as the mark to follow from."""
        session = self._box.open_session()
        if not session.has_capability("IDLE"):
            self.unsupported = f"mailbox {self.name}: the server lacks IDLE, so nothing is pushed"
            session.logout()
            return False
        status = session.select_folder(FOLDER)
        uidvalidity = status[b"UIDVALIDITY"]
        if self._mark is None or uidvalidity != self._uidvalidity:
            self._mark = status[b"UIDNEXT"] - 1
            if self._uidvalidity is not None:
                self._publish(self.name, {"folder": FOLDER, "uidvalidity_changed": uidvalidity})
            self._uidvalidity = uidvalidity
        self._session = session
        return True

    def step(self):
        """Wait in IDLE until the server speaks or the renewal is due, then publish what is new."""
        session = self._session
        session.idle()
        try:
            session.idle_check(timeout=self._idle_seconds)
        finally:
            session.idle_done()
        status = session.select_folder(FOLDER)
        uidvalidity = status[b"UIDVALIDITY"]
        if uidvalidity != self._uidvalidity:
            # Every UID taken before now names other mail, so the mark starts over at
            # the newest message and the subscribers are told why
            self._uidvalidity = uidvalidity
            self._mark = status[b"UIDNEXT"] - 1
            self._publish(self.name, {"folder": FOLDER, "uidvalidity_changed": uidvalidity})
            return
        # UID n:* names the highest message even when n is above it, so the range is
        # filtered as well as asked for
        new = sorted(u for u in session.search(["UID", f"{self._mark + 1}:*"]) if u > self._mark)
        if not new:
            return
        fetched = session.fetch(new, ["BODY.PEEK[HEADER]"])
        for uid in new:
            if uid in fetched:
                self._publish(
                    self.name,
                    {
                        "folder": FOLDER,
                        "uid": uid,
                        "uidvalidity": uidvalidity,
                        "header": fetched[uid][b"BODY[HEADER]"],
                    },
                )
            self._mark = uid

    def run(self, *, rounds=None, retry_seconds=RETRY_SECONDS):
        """Loop until stopped: connect, step, and connect again when the session drops."""
        done = 0
        while not self.stopped.is_set() and (rounds is None or done < rounds):
            done += 1
            try:
                if self._session is None and not self.connect():
                    return
                self.step()
            except client.AuthFailed as refused:
                self.unsupported = str(refused)
                return
            except client.LOST:
                self._session = None
                self.stopped.wait(retry_seconds)

    def stop(self):
        self.stopped.set()


def start(box, publish):
    """Run a watcher for this mailbox in a daemon thread, and hand it back."""
    watcher = Watcher(box, publish)
    threading.Thread(target=watcher.run, name=f"watch-{box.config.name}", daemon=True).start()
    return watcher
