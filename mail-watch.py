#!/usr/bin/env python3
"""Follow a mailbox's INBOX and print each new message the grant reaches as one line.

Every line is an event: the UID, the sender's address and the subject with newlines
folded away, so a reader that consumes lines sees one message per line. The service
pushes each message as it lands, and a read catches up on what came while no stream was
open. That read asks from the last UID seen rather than for a count of recent ones, so
nothing between two rounds is lost or shown twice.

A UID means something only within its folder's UIDVALIDITY, so the mark is the pair. A
rebuilt INBOX starts the mark over at its newest message, and the watcher says so.

What mail.py sent is left out by its Message-ID: the agent wrote it and already knows
it. A hook starts the watcher fresh on every run, so the mark can live in a file, and a
command can be run for each new message instead of reading the lines.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mail  # noqa: E402  — the client beside this file, which owns talking to the socket

DEFAULT_INTERVAL = 5
BATCH = 100


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mail-watch.py",
        description="Print each new message in a mailbox's INBOX as it arrives, one line each",
        epilog=(
            "The mailbox needs read on some address, like any other reading, and only\n"
            "messages a rule reaches are shown.\n\n"
            "Examples:\n"
            "  mail-watch.py --mailbox work\n"
            "  mail-watch.py --mailbox work --once --state ~/.local/state/mail-agent/work\n"
            "  mail-watch.py --mailbox work --exec 'notify-send \"$(jq -r .subject)\"'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mailbox", required=True, help="the mailbox to follow")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="seconds to wait before asking again when the service cannot be reached "
        "or refused; a stream the service closed is opened again at once "
        "(default: %(default)s)",
    )
    parser.add_argument("--once", action="store_true", help="print what is new and exit, instead of following")
    parser.add_argument(
        "--state",
        help="a file holding the last UIDVALIDITY and UID seen, read at start and written "
        "after each message, so a watcher started again by a hook resumes where it stopped",
    )
    parser.add_argument(
        "--exec",
        help="a shell command run for each new message, with the message as one JSON "
        "line on its standard input; the lines are still printed",
    )
    parser.add_argument(
        "--socket",
        default=mail.DEFAULT_SOCKET,
        help="the service socket (default: $MAIL_SOCKET, else %(default)s)",
    )
    return parser


def line_for(message):
    """One event as one line: nothing in it may contain a newline."""
    senders = message.get("from") or [{}]
    who = senders[0].get("address") or "[no sender]"
    subject = " ".join((message.get("subject") or "[no subject]").split())
    return f"{message['uid']} {who} {subject}"


def read(options, since=None, limit=BATCH):
    request = {"action": "read", "mailbox": options.mailbox, "limit": limit}
    if since is not None:
        request["since_uid"] = since
    answer = mail.ask(options.socket, request)
    if not answer.get("ok"):
        raise Refused(answer.get("error"))
    return answer["result"]


class Refused(Exception):
    """The service refused, and the mark stays where it was."""


def resume(options, mark):
    """The mark to go on from: the one given, unless the folder was rebuilt since.

    With no mark, or a mark from before a rebuild, it is the folder's newest message, so
    nothing already there is shown.
    """
    result = read(options, limit=1)
    if mark is None or mark[0] != result["uidvalidity"]:
        if mark is not None:
            print("mail-watch.py: INBOX was rebuilt, starting from its newest message", file=sys.stderr)
        return (result["uidvalidity"], result["last_uid"] or 0)
    return mark


def catch_up(options, mark):
    """Show everything after the mark, oldest first, page by page.

    Mail the grant does not reach still moves the mark past it, so a page of withheld
    messages is stepped over rather than asked for again.
    """
    while True:
        result = read(options, since=mark[1])
        if result["uidvalidity"] != mark[0]:
            return resume(options, mark)
        for message in result["messages"]:
            mark = shown(options, message, mark)
        reached = result["last_uid"] if not result.get("more") else result.get("scanned_to")
        mark = (mark[0], max(mark[1], reached or 0))
        if not result.get("more"):
            return mark


def follow(options, mark):
    """Subscribe, catch up by reading, then show each pushed message as it arrives.

    The subscription is opened before the catch-up read, so a message that lands during
    the read is pushed as well, and the mark keeps it from being shown twice. Answers
    with the mark to resume from, and whether to resume at once: a stream the service
    closed can be opened again now, a refusal would only be repeated.
    """
    answers = mail.stream(options.socket, {"action": "subscribe", "mailbox": options.mailbox})
    first = next(answers, None)
    if first is None or not first.get("ok"):
        if first is not None:
            print(f"refused: {first.get('error')}", flush=True)
        return mark, False
    try:
        mark = catch_up(options, mark)
    except Refused as refused:
        print(f"refused: {refused}", flush=True)
        return mark, False
    for answer in answers:
        if not answer.get("ok"):
            print(f"refused: {answer.get('error')}", flush=True)
            return mark, False
        if "uidvalidity_changed" in answer:
            return resume(options, mark), True
        mark = shown(options, answer["message"], mark)
    return mark, True


def shown(options, message, mark):
    """Show one message unless it is at or below the mark or the agent sent it.

    Answers with the mark after it, which is kept in the state file at once, so a
    watcher stopped mid-stream resumes after the last message it handled.
    """
    if message["uidvalidity"] == mark[0] and message["uid"] <= mark[1]:
        return mark
    if message.get("message_id") not in mail.SENT.ids(options.mailbox):
        print(line_for(message), flush=True)
        if options.exec:
            run_for(options, message)
    mark = (message["uidvalidity"], message["uid"])
    if options.state:
        write_mark(options.state, mark)
    return mark


def step(options, mark):
    """One subscription, from its opening to its end: the mark, and whether to go on now.

    A service that cannot be reached is reported and waited out rather than ending the
    watch: a system rebuild replaces its socket, which takes a few seconds to come back.
    """
    try:
        return follow(options, mark)
    except OSError as unreachable:
        print(f"mail-watch.py: {options.socket}: {unreachable}", file=sys.stderr)
        return mark, False


def run_for(options, message):
    """Run the caller's command on one message. Its failure is reported, not fatal."""
    event = json.dumps({"mailbox": options.mailbox, **message}, ensure_ascii=False)
    status = subprocess.run(options.exec, shell=True, input=event + "\n", text=True).returncode
    if status:
        print(f"mail-watch.py: --exec exited {status} on {message['uid']}", file=sys.stderr)


def read_mark(path):
    try:
        with open(path, encoding="utf-8") as kept:
            uidvalidity, uid = kept.read().split()
        return (int(uidvalidity), int(uid))
    except (FileNotFoundError, ValueError):
        return None


def write_mark(path, mark):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"{mark[0]} {mark[1]}\n", encoding="utf-8")


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        mark = read_mark(options.state) if options.state else None
        mark = resume(options, mark)
        if options.once:
            mark = catch_up(options, mark)
            if options.state:
                write_mark(options.state, mark)
            return 0
        while True:
            mark, now = step(options, mark)
            if options.state:
                write_mark(options.state, mark)
            if not now:
                time.sleep(options.interval)
    except KeyboardInterrupt:
        return 0
    except Refused as refused:
        print(f"mail-watch.py: {refused}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as unreachable:
        print(f"mail-watch.py: {options.socket}: {unreachable}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
