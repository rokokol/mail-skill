"""A subscription passes on each new message under the grant as it is at that moment.

A message no rule reaches is not passed on at all: not its sender, not that it arrived.
A grant withdrawn while the stream runs ends it at the next message. A subscriber that
stops reading is cut off with a reason, and catches up by reading from its mark.
"""

import asyncio
import json

from tests.conftest import mail


# A scenario takes milliseconds. A broken guard can leave a stream waiting for a message
# that never comes, and the deadline turns that into a failure instead of a stalled run
DEADLINE = 1


def following(stream):
    return asyncio.wait_for(anext(stream), DEADLINE)


def header(raw):
    return raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"


def event(uid, raw, uidvalidity=1000):
    return {"folder": "INBOX", "uid": uid, "uidvalidity": uidvalidity, "header": header(raw)}


def run(world, scenario):
    asyncio.run(scenario(world.handler.stream({"action": "subscribe", "mailbox": "work"})))


def test_subscribing_needs_read_somewhere_in_the_file(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: send\n")

    async def scenario(stream):
        refused = await following(stream)
        assert refused["ok"] is False and "read" in refused["remedy"]
        assert world.hub.count("work") == 0

    run(world, scenario)


def test_a_covered_message_arrives_and_an_uncovered_one_never_does(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: read\n")

    async def scenario(stream):
        ack = await following(stream)
        assert ack["ok"] and ack["result"]["mailbox"] == "work"
        world.hub.publish("work", event(7, mail("stranger@x.org", subject="secret plan")))
        world.hub.publish("work", event(8, mail("boss@corp.com", subject="granted")))
        arrived = await following(stream)
        assert arrived["message"]["uid"] == 8 and arrived["message"]["subject"] == "granted"
        assert arrived["message"]["allowed_by"] == "read"
        assert "secret plan" not in json.dumps(arrived)
        await stream.aclose()
        assert world.hub.count("work") == 0

    run(world, scenario)


def test_the_grant_is_read_again_for_every_message(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: read\n")

    async def scenario(stream):
        await following(stream)
        world.grant("work", "allow boss@corp.com: send\n")
        world.hub.publish("work", event(9, mail("boss@corp.com", subject="after the change")))
        ended = await following(stream)
        assert ended["ok"] is False and "read" in ended["remedy"]
        assert "after the change" not in json.dumps(ended)

    run(world, scenario)


def test_a_rebuilt_inbox_is_passed_on(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: read\n")

    async def scenario(stream):
        await following(stream)
        world.hub.publish("work", {"folder": "INBOX", "uidvalidity_changed": 2000})
        changed = await following(stream)
        assert changed["ok"] and changed["uidvalidity_changed"] == 2000

    run(world, scenario)


def test_a_subscriber_that_falls_behind_is_cut_off_with_a_reason(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: read\n")
    world.hub.depth = 2

    async def scenario(stream):
        await following(stream)
        for uid in range(3):
            world.hub.publish("work", event(uid + 10, mail("boss@corp.com")))
        cut = await following(stream)
        assert cut["ok"] is False and "fell behind" in cut["error"]

    run(world, scenario)


def test_a_mailbox_without_a_watch_refuses_the_subscription(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow boss@corp.com: read\n")

    class Deaf:
        unsupported = "mailbox work: the server lacks IDLE, so nothing is pushed"

    world.watchers["work"] = Deaf()

    async def scenario(stream):
        refused = await following(stream)
        assert refused["ok"] is False and "IDLE" in refused["error"]

    run(world, scenario)


def test_a_to_grant_lets_mail_to_that_alias_through(world):
    world.mailbox("work", "work@example.org", own=["github@example.org"])
    world.grant("work", "allow to github@example.org: read\n")

    async def scenario(stream):
        await following(stream)
        world.hub.publish("work", event(3, mail("someone@x.org", to="work@example.org", subject="no")))
        world.hub.publish("work", event(4, mail("bot@github.com", to="github@example.org", subject="ci")))
        arrived = await following(stream)
        assert arrived["message"]["subject"] == "ci"

    run(world, scenario)
