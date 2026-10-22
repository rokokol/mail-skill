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

from . import addresses, client, media, message, permissions, verbs

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# How many messages a listing looks at, newest first, before it stops. A folder with
# years of mail would otherwise be read whole to find twenty messages from one sender
SCAN = 500
BATCH = 50


class BadRequest(ValueError):
    """The request is missing a field or holds one that cannot mean anything."""


class UnknownMailbox(LookupError):
    """No mailbox of that name is configured."""


class Handler:
    """Answers requests against one permission store and the configured mailboxes."""

    def __init__(self, store, boxes, outbox=None, hub=None):
        self._store = store
        self._boxes = boxes
        self._outbox = outbox
        self.hub = hub
        self._locks = {}

    async def handle(self, request):
        try:
            return await self._dispatch(request)
        except (permissions.BadName, UnknownMailbox, BadRequest) as bad:
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
        }.get(action)
        if perform is None:
            return _refused(f"no action is called {action!r}")
        name = permissions.as_name(request.get("mailbox", ""))
        box = self._boxes.get(name)
        if box is None:
            raise UnknownMailbox(f"no mailbox is called {name!r}")
        return await perform(name, box, request)

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
        shown, withheld, scanned = [], 0, 0
        for start in range(0, len(order), BATCH):
            batch = order[start : start + BATCH]
            fetched = await self._call(name, box.headers, folder, batch, uidvalidity=uidvalidity)
            for uid in batch:
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
                "more": scanned < len(uids),
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
