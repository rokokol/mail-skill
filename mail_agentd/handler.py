"""Turn one request into one answer, deciding by the permission files alone.

Every answer says what decided it. An allowed action names the granted word and the line
that held it, and says when the word was the wildcard. A refusal names the line that
would lift it, so the user can act on it without reading the vocabulary first.

A message out of reach is not described at all. A listing says how many it held back,
and nothing else about them, so a summary is never mistaken for the whole folder.

Nothing here trusts the request. The mailbox name is validated before it becomes a path,
the action is looked up in the vocabulary before a server is touched, and a broken
permission file is an answer rather than a crash.
"""

import asyncio
import email
import email.policy

from . import addresses, client, compose, media, message, permissions, verbs
from .hub import FellBehind

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# How many messages a listing looks at, newest first, before it stops. A folder with
# years of mail would otherwise be read whole to find twenty messages from one sender
SCAN = 500
BATCH = 50

ANSWERED = b"\\Answered"
DRAFT = b"\\Draft"


class BadRequest(ValueError):
    """The request is missing a field or holds one that cannot mean anything."""


class UnknownMailbox(LookupError):
    """No mailbox of that name is configured."""


class Handler:
    """Answers requests against one permission store and the configured mailboxes."""

    def __init__(self, store, boxes, outbox=None, hub=None, watchers=None):
        self._store = store
        self._boxes = boxes
        self._outbox = outbox
        self.hub = hub
        self._watchers = watchers if watchers is not None else {}
        self._locks = {}

    async def handle(self, request):
        try:
            return await self._dispatch(request)
        except (permissions.BadName, UnknownMailbox, BadRequest, compose.BadMessage) as bad:
            return _refused(str(bad))
        except permissions.PermissionFileError as broken:
            return _refused(str(broken))
        except (client.Moved, client.NoFolder, client.Unsupported, client.AuthFailed) as plain:
            return _refused(str(plain))
        except media.BadDestination as bad:
            return _refused(str(bad))
        except Exception as unexpected:
            # Anything else has to come back as an answer too. Letting it close the
            # connection leaves the caller with an empty read and no way to tell a failure
            # from a folder that holds nothing
            return _refused(f"{type(unexpected).__name__}: {unexpected}")

    async def _dispatch(self, request):
        if not isinstance(request, dict):
            raise BadRequest("a request is one JSON object")
        action = request.get("action", "")

        # Asking what is held reaches no server and needs no grant. Without it the agent
        # can only discover its permissions by attempting actions and reading refusals
        if action == "permissions":
            return _allowed(None, action, self._store.survey())
        if action == "mailboxes":
            listed = [{"name": n, "address": b.config.address} for n, b in sorted(self._boxes.items())]
            return _allowed(None, action, listed)
        if action == "subscribe":
            return _refused("subscribe streams, so it is served by stream, not handle")

        perform = {
            "list": self._list,
            "read": self._read,
            "media": self._media,
            "send": self._send,
            "reply": self._reply,
            "forward": self._forward,
            "draft": self._draft,
            "mark-read": self._change,
            "move": self._change,
            "delete": self._change,
            "expunge": self._change,
        }.get(action)
        if perform is None:
            return _refused(f"no action is called {action!r}")
        name = permissions.as_name(request.get("mailbox", ""))
        box = self._boxes.get(name)
        if box is None:
            raise UnknownMailbox(f"no mailbox is called {name!r}")
        return await perform(name, box, request)

    async def stream(self, request):
        """Answer a subscription: one answer for the grant, then one per new message.

        The file is read again for every message. A message no rule reaches is passed
        over in silence, since even its arrival is something the grant did not give. A
        file that no longer grants read anywhere ends the stream, the way a withdrawn
        permission should. The subscription is taken before the first answer, so nothing
        that lands in between is lost.
        """
        try:
            name = permissions.as_name(request.get("mailbox", ""))
            box = self._boxes.get(name)
            if box is None:
                raise UnknownMailbox(f"no mailbox is called {name!r}")
            refusal = self._reads_anything(name)
        except (permissions.BadName, UnknownMailbox, permissions.PermissionFileError) as bad:
            yield _refused(str(bad))
            return
        if refusal is not None:
            yield refusal
            return
        watcher = self._watchers.get(name)
        if watcher is not None and watcher.unsupported:
            yield _refused(watcher.unsupported)
            return
        if self.hub is None:
            yield _refused("this service holds no watch, so nothing is pushed")
            return
        subscription = self.hub.subscribe(name)
        try:
            yield _allowed("read", "subscribe", {"mailbox": name, "folder": "INBOX"})
            while True:
                try:
                    arrived = await subscription.next()
                except FellBehind:
                    yield _refused(
                        "this subscriber fell behind and missed messages; read from the "
                        "last uid seen, then subscribe again"
                    )
                    return
                if "uidvalidity_changed" in arrived:
                    yield {"ok": True, **arrived}
                    continue
                try:
                    refusal = self._reads_anything(name)
                    decision = None if refusal else self.decide(self._store.grant(name), "read", arrived["header"], box)
                except permissions.PermissionFileError as broken:
                    refusal = _refused(str(broken))
                if refusal is not None:
                    yield refusal
                    return
                if not decision.allowed:
                    continue
                identity = _identity(name, arrived["folder"], arrived["uid"], arrived["uidvalidity"])
                described = message.describe(arrived["header"], full=False, identity=identity)
                described |= {"allowed_by": decision.by, "via": decision.via}
                yield {"ok": True, "message": described}
        finally:
            self.hub.unsubscribe(name, subscription)

    def _reads_anything(self, name):
        """A refusal when no line of the mailbox's file grants read, else None."""
        grant = self._store.grant(name)
        if not grant.reads_anything():
            return _refused(
                f"mailbox {name} grants read on no address",
                remedy=f"add read to an 'allow ADDRESS:' line in mailboxes/{name}.conf",
            )
        return None

    async def _call(self, name, command, *args, **kwargs):
        """Run one blocking command on a mailbox, one at a time per mailbox."""
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(command, *args, **kwargs)

    def own_rules(self, box):
        """The rules naming the mailbox's own addresses: its address, then the configured ones."""
        return [addresses.parse_rule(box.config.address)] + [
            addresses.parse_rule(text) for text in box.config.own
        ]

    def decide(self, grant, action, raw, box):
        """Decide a message action on the bytes of one message, its header block at least."""
        parsed = message.parse(raw)
        counterparts, reached = message.split_own(message.participants(parsed), self.own_rules(box))
        return grant.may_message(action, counterparts, reached)

    async def _list(self, name, box, request):
        decision = self._store.grant(name).may_mailbox("list")
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        return _allowed(decision.by, "list", await self._call(name, box.folders), via=decision.via)

    async def _read(self, name, box, request):
        if request.get("uid") is not None:
            return await self._read_one(name, box, request)
        folder = _folder(request)
        limit = max(1, min(_integer(request, "limit", DEFAULT_LIMIT), MAX_LIMIT))
        since = _integer(request, "since_uid", None)
        criteria = []
        if request.get("unseen"):
            criteria.append("UNSEEN")
        if request.get("search"):
            criteria += ["TEXT", str(request["search"])]
        uidvalidity = await self._call(name, box.uidvalidity, folder)
        uids = await self._call(
            name, box.uids, folder, after=since, criteria=criteria, uidvalidity=request.get("uidvalidity")
        )
        # A catch-up from a mark wants the oldest first, so it can go on from the last one
        # shown; a plain listing wants the newest
        order = uids[:SCAN] if since is not None else list(reversed(uids))[:SCAN]
        grant = self._store.grant(name)
        shown, withheld, scanned, examined = [], 0, 0, []
        for start in range(0, len(order), BATCH):
            batch = order[start : start + BATCH]
            fetched = await self._call(name, box.headers, folder, batch, uidvalidity=uidvalidity)
            for uid in batch:
                examined.append(uid)
                if uid not in fetched:
                    continue
                scanned += 1
                head = fetched[uid]["header"]
                decision = self.decide(grant, "read", head, box)
                if not decision.allowed:
                    withheld += 1
                    continue
                described = message.describe(
                    head, full=False, identity=_identity(name, folder, uid, uidvalidity)
                )
                described |= {
                    "flags": fetched[uid]["flags"],
                    "size": fetched[uid]["size"],
                    "allowed_by": decision.by,
                    "via": decision.via,
                }
                shown.append(described)
                if len(shown) == limit:
                    break
            if len(shown) == limit:
                break
        if since is None:
            shown.reverse()
        by_words = {m["allowed_by"] for m in shown}
        by = verbs.WILDCARD if verbs.WILDCARD in by_words else ("read" if shown else None)
        return _allowed(
            by,
            "read",
            {
                "folder": folder,
                "uidvalidity": uidvalidity,
                "messages": shown,
                "withheld": withheld,
                "scanned": scanned,
                "more": len(examined) < len(uids),
                # Where the look stopped. A catch-up goes on from here, past the messages
                # it was not shown, or a page of withheld mail would hold it in place
                "scanned_to": examined[-1] if examined else since,
                # The highest UID the folder holds, granted or not, so a watcher can start
                # from now without being shown what came before. It names no message
                "last_uid": uids[-1] if uids else since,
            },
        )

    async def _message(self, name, box, request, action):
        """Fetch one message and decide an action on it: (raw, decision, identity)."""
        folder = _folder(request)
        uid = _integer(request, "uid", None)
        if uid is None:
            raise BadRequest(f"{action} names no uid")
        uidvalidity = request.get("uidvalidity")
        current = await self._call(name, box.uidvalidity, folder)
        if uidvalidity is not None and int(uidvalidity) != current:
            raise client.Moved(
                f"folder {folder} was rebuilt: its UIDVALIDITY is {current}, not {uidvalidity}, "
                f"so its identifiers name other messages now"
            )
        raw = await self._call(name, box.body, folder, uid, uidvalidity=current)
        if raw is None:
            raise BadRequest(f"folder {folder} holds no message {uid}")
        decision = self.decide(self._store.grant(name), action, raw, box)
        return raw, decision, _identity(name, folder, uid, current)

    async def _read_one(self, name, box, request):
        raw, decision, identity = await self._message(name, box, request, "read")
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        return _allowed(decision.by, "read", message.describe(raw, full=True, identity=identity), via=decision.via)

    async def _media(self, name, box, request):
        if self._outbox is None:
            return _refused("this service has no outbox, so nothing can be downloaded")
        raw, decision, identity = await self._message(name, box, request, "media")
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        wanted = request.get("indexes")
        parts = message.attachments(raw)
        files, missing = [], []
        for index, part in enumerate(parts):
            if wanted is not None and index not in wanted:
                continue
            path = self._outbox.place(name, f"{identity['uid']}-{index}-{part.get_filename() or 'part'}")
            path.write_bytes(part.get_payload(decode=True) or b"")
            files.append({"index": index, "name": part.get_filename(), "path": str(path)})
        if wanted is not None:
            missing = [i for i in wanted if not 0 <= i < len(parts)]
        return _allowed(
            decision.by, "media", {**identity, "files": files, "missing": missing}, via=decision.via
        )

    async def _send(self, name, box, request):
        lists = compose.recipients(request)
        decision = self._store.grant(name).may_recipients("send", _everyone(lists))
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        msg = compose.build(
            sender=box.config.address,
            to=lists["to"],
            cc=lists["cc"],
            subject=request.get("subject", ""),
            text=request.get("text", ""),
            html=request.get("html"),
            attached=compose.files(request),
        )
        return await self._submit(name, box, "send", decision, msg, lists)

    async def _change(self, name, box, request):
        """Mark, move, delete or expunge messages: all of them, or none when one is out of reach."""
        action = request["action"]
        folder = _folder(request)
        uids = request.get("uids")
        if uids is None and request.get("uid") is not None:
            uids = [request["uid"]]
        try:
            uids = [int(u) for u in uids or []]
        except (TypeError, ValueError):
            raise BadRequest("uids is a list of numbers") from None
        if not uids:
            raise BadRequest(f"{action} names no uid")
        target = request.get("to_folder")
        if action == "move" and not (isinstance(target, str) and target):
            raise BadRequest("move names no to_folder")
        current = await self._call(name, box.uidvalidity, folder)
        uidvalidity = request.get("uidvalidity")
        if uidvalidity is not None and int(uidvalidity) != current:
            raise client.Moved(
                f"folder {folder} was rebuilt: its UIDVALIDITY is {current}, not {uidvalidity}, "
                f"so its identifiers name other messages now"
            )
        fetched = await self._call(name, box.headers, folder, uids, uidvalidity=current)
        gone = [u for u in uids if u not in fetched]
        if gone:
            raise BadRequest(f"folder {folder} holds no message {', '.join(map(str, gone))}")
        grant = self._store.grant(name)
        words = set()
        for uid in uids:
            decision = self.decide(grant, action, fetched[uid]["header"], box)
            if not decision.allowed:
                return _refused(f"message {uid}: {decision.reason}", remedy=decision.remedy)
            words.add(decision.by)
        if action == "mark-read":
            await self._call(name, box.set_seen, folder, uids, bool(request.get("seen", True)), uidvalidity=current)
        elif action == "move":
            await self._call(name, box.move, folder, uids, target, uidvalidity=current)
        elif action == "delete":
            await self._call(name, box.trash, folder, uids, uidvalidity=current)
        else:
            await self._call(name, box.expunge, folder, uids, uidvalidity=current)
        by = verbs.WILDCARD if verbs.WILDCARD in words else action
        return _allowed(by, action, {"mailbox": name, "folder": folder, "uids": uids, "uidvalidity": current})

    async def _reply(self, name, box, request):
        raw, read, identity = await self._message(name, box, request, "read")
        if not read.allowed:
            return _refused(read.reason, remedy=read.remedy)
        parsed = message.parse(raw)
        own = self.own_rules(box)
        # The answer goes where the message asks answers to go, and that is the address
        # the grant has to cover: Reply-To may point anywhere its writer liked
        targets = addresses.addresses_in(parsed.get_all("Reply-To", []) or parsed.get_all("From", []))
        if request.get("all"):
            targets += message.participants(parsed)
        counterparts, _ = message.split_own(list(dict.fromkeys(targets)), own)
        decision = self._store.grant(name).may_recipients("reply", counterparts)
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        original_id = compose.carried(parsed.get("Message-ID"))
        references = " ".join(filter(None, [compose.carried(parsed.get("References")), original_id]))
        subject = compose.carried(message.describe(raw, full=False)["subject"])
        sender = message.own_reached(parsed, own) or box.config.address
        msg = compose.build(
            sender=sender,
            to=counterparts,
            cc=[],
            subject=subject if subject.lower().startswith("re:") else f"Re: {subject}",
            text=request.get("text", ""),
            html=request.get("html"),
            attached=compose.files(request),
            in_reply_to=original_id or None,
            references=references or None,
        )
        answer = await self._submit(name, box, "reply", decision, msg, {"to": counterparts}, sender=sender)
        folder, uid, uidvalidity = identity["folder"], identity["uid"], identity["uidvalidity"]
        await self._call(name, box.set_flag, folder, [uid], ANSWERED, uidvalidity=uidvalidity)
        # A reply with no read receipt before it is a sequence no person produces, and
        # only the file can say the user wants the receipt sent
        if self._store.mailbox_flag(name, "mark-read-on-reply"):
            await self._call(name, box.set_seen, folder, [uid], True, uidvalidity=uidvalidity)
        answer["result"]["in_reply_to"] = identity
        return answer

    async def _forward(self, name, box, request):
        raw, read, identity = await self._message(name, box, request, "read")
        if not read.allowed:
            return _refused(read.reason, remedy=read.remedy)
        lists = compose.recipients(request)
        decision = self._store.grant(name).may_recipients("forward", _everyone(lists))
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        parsed = message.parse(raw)
        subject = compose.carried(message.describe(raw, full=False)["subject"])
        original = email.message_from_bytes(raw, policy=email.policy.default)
        sender = message.own_reached(parsed, self.own_rules(box)) or box.config.address
        msg = compose.build(
            sender=sender,
            to=lists["to"],
            cc=lists["cc"],
            subject=subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}",
            text=request.get("text", ""),
            extra=[original],
        )
        answer = await self._submit(name, box, "forward", decision, msg, lists, sender=sender)
        answer["result"]["forwarded"] = identity
        return answer

    async def _draft(self, name, box, request):
        lists = compose.recipients(request)
        # A draft the user may later send from their own client without reading it is a
        # send with a delay, so it is held to every recipient the same way
        decision = self._store.grant(name).may_recipients("draft", _everyone(lists))
        if not decision.allowed:
            return _refused(decision.reason, remedy=decision.remedy)
        msg = compose.build(
            sender=box.config.address,
            to=lists["to"],
            cc=lists["cc"],
            draft_bcc=lists["bcc"],
            subject=request.get("subject", ""),
            text=request.get("text", ""),
            html=request.get("html"),
            attached=compose.files(request),
        )
        drafts = await self._call(name, box.special, "drafts")
        await self._call(name, box.append, drafts, compose.as_bytes(msg), [DRAFT, client.SEEN])
        return _allowed(
            decision.by,
            "draft",
            {"message_id": msg["Message-ID"], "folder": drafts, **lists},
            via=decision.via,
        )

    async def _submit(self, name, box, action, decision, msg, lists, sender=None):
        """Send the message to every listed address and file a copy, Bcc nowhere in it.

        The copy is filed after the send, and a copy that cannot be filed is a warning,
        not a failure: the message has already left, and saying otherwise would invite a
        second send.
        """
        raw = compose.as_bytes(msg)
        sender = sender or box.config.address
        await self._call(name, box.send, sender, _everyone(lists), raw)
        warnings = []
        if box.config.append_sent:
            try:
                sent = await self._call(name, box.special, "sent")
                await self._call(name, box.append, sent, raw, [client.SEEN])
            except (client.NoFolder, *client.LOST) as unfiled:
                warnings.append(f"sent, but no copy was filed in Sent: {unfiled}")
        return _allowed(
            decision.by,
            action,
            {"message_id": msg["Message-ID"], "from": sender, **lists, "warnings": warnings},
            via=decision.via,
        )


def _folder(request):
    folder = request.get("folder", "INBOX")
    if not isinstance(folder, str) or not folder:
        raise BadRequest("folder is a folder name")
    return folder


def _integer(request, key, default):
    value = request.get(key, default)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{key} is a number, not {value!r}") from None


def _everyone(lists):
    """Every recipient once, in To, Cc, Bcc order."""
    seen = []
    for field in ("to", "cc", "bcc"):
        for address in lists.get(field, []):
            if address not in seen:
                seen.append(address)
    return seen


def _identity(name, folder, uid, uidvalidity):
    return {"mailbox": name, "folder": folder, "uid": uid, "uidvalidity": uidvalidity}


def _allowed(by, action, result, via=None):
    answer = {
        "ok": True,
        "allowed_by": by,
        "wildcard": by == verbs.WILDCARD,
        "action": action,
        "result": result,
    }
    if via:
        answer["via"] = via
    return answer


def _refused(error, remedy=None):
    answer = {"ok": False, "error": error}
    if remedy:
        answer["remedy"] = remedy
    return answer
