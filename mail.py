#!/usr/bin/env python3
"""Talk to the mail-agentd socket. One request per call, one answer printed as JSON.

This half holds no secret and makes no decision. It sends what it is told and prints what
comes back, so the standard library is all it needs and it runs under whatever python is
at hand. Everything that decides lives behind the socket. Talking to the socket is
agentsock.py beside this file.
"""

import argparse
import base64
import json
import os
import sys

from agentsock import SentLog, ask, stream  # noqa: F401  — stream is for mail-watch.py

DEFAULT_SOCKET = os.environ.get("MAIL_SOCKET", "/run/mail-agent/socket")

# Messages this client sent, by Message-ID, so a watcher can leave out the agent's own
# mail when it comes back, to an own address or a list the mailbox is on
SENT = SentLog("MAIL_SENT_LOG", "mail-agent")

SENDS = ("send", "reply", "forward")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mail.py",
        description="Send one request to the mail service and print its answer",
        epilog=(
            "The service decides. A refusal names the permission line that would lift "
            "it; take that line to the user rather than working around it.\n\n"
            "Actions: permissions, mailboxes, list, read, media, send, reply, forward,\n"
            "draft, mark-read, move, delete, expunge\n\n"
            "Examples:\n"
            "  mail.py permissions\n"
            "  mail.py read --mailbox work --limit 10\n"
            "  mail.py read --mailbox work --unseen --search invoice\n"
            "  mail.py read --mailbox work --uid 4211\n"
            "  mail.py reply --mailbox work --uid 4211 --text-file answer.txt\n"
            "  mail.py send --mailbox work --to boss@corp.com --subject Report --file report.pdf\n"
            "  mail.py draft --mailbox work --to boss@corp.com --subject Later --text 'to finish'\n"
            "  mail.py delete --mailbox work --uids 4211 4212\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", help="the action to ask for; 'permissions' lists what is held")
    parser.add_argument("--mailbox", help="the mailbox, by the name 'mailboxes' lists")
    parser.add_argument("--folder", help="the folder (default: INBOX)")
    parser.add_argument("--uid", type=int, help="one message, for read, media, reply and forward")
    parser.add_argument("--uids", type=int, nargs="+", help="messages, for mark-read, move, delete and expunge")
    parser.add_argument(
        "--uidvalidity",
        type=int,
        help="the folder's UIDVALIDITY the uid was read under; a rebuilt folder is then refused",
    )
    parser.add_argument("--to", nargs="+", help="recipients, one bare address each")
    parser.add_argument("--cc", nargs="+", help="copy recipients")
    parser.add_argument("--bcc", nargs="+", help="hidden recipients, named in the envelope alone")
    parser.add_argument("--subject", help="the subject of send and draft")
    parser.add_argument("--text", help="the plain text body")
    parser.add_argument("--text-file", help="a file holding the plain text body")
    parser.add_argument("--html", help="an HTML body beside the text")
    parser.add_argument(
        "--file",
        action="append",
        help="a file to attach, repeatable; this client reads it and the service never opens a path",
    )
    parser.add_argument("--all", action="store_true", help="reply to every participant, not only the sender")
    parser.add_argument("--limit", type=int, help="how many messages a listing shows")
    parser.add_argument("--since-uid", type=int, help="only messages after this uid, oldest first")
    parser.add_argument("--search", help="only messages holding this text")
    parser.add_argument("--unseen", action="store_true", help="only unread messages")
    parser.add_argument("--unread", action="store_true", help="for mark-read: mark unread instead")
    parser.add_argument("--to-folder", help="the destination of move")
    parser.add_argument("--indexes", type=int, nargs="+", help="which attachments media downloads")
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET,
        help="the service socket (default: $MAIL_SOCKET, else %(default)s)",
    )
    return parser


def request_from(options):
    """Everything the user named, and nothing they did not."""
    named = {
        "action": options.action,
        "mailbox": options.mailbox,
        "folder": options.folder,
        "uid": options.uid,
        "uids": options.uids,
        "uidvalidity": options.uidvalidity,
        "to": options.to,
        "cc": options.cc,
        "bcc": options.bcc,
        "subject": options.subject,
        "text": options.text,
        "html": options.html,
        "limit": options.limit,
        "since_uid": options.since_uid,
        "search": options.search,
        "to_folder": options.to_folder,
        "indexes": options.indexes,
    }
    request = {key: value for key, value in named.items() if value is not None}
    if options.text_file:
        with open(options.text_file, encoding="utf-8") as body:
            request["text"] = body.read()
    if options.all:
        request["all"] = True
    if options.unseen:
        request["unseen"] = True
    if options.unread:
        request["seen"] = False
    if options.file:
        request["files"] = []
        for path in options.file:
            with open(path, "rb") as attached:
                data = attached.read()
            request["files"].append(
                {"name": os.path.basename(path), "data": base64.b64encode(data).decode()}
            )
    return request


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        answer = ask(options.socket, request_from(options))
    except (OSError, json.JSONDecodeError) as unreachable:
        print(f"mail.py: {options.socket}: {unreachable}", file=sys.stderr)
        return 2
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    if options.action in SENDS and options.mailbox:
        # Only an answer that sent something carries the message it made
        sent = (answer.get("result") or {}).get("message_id")
        if sent:
            SENT.remember(options.mailbox, sent)
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
