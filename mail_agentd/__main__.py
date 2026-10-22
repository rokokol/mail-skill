"""Start the service: read the mailboxes and their passwords, and serve the socket."""

import argparse
import asyncio
import json
import os
import smtplib
import ssl
import sys
from pathlib import Path

from . import addresses, client, handler, hub, media, permissions, server, verbs

# A missing password or a mailbox that cannot be configured is a fault a restart cannot
# fix. The unit stops on this code instead of waking every few seconds for as long as
# the machine is up
EX_CONFIG = 78

# How long one IMAP or SMTP exchange may take before the connection counts as lost
TIMEOUT = 60


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mail-agentd",
        description="Serve mailboxes over a unix socket, under per-mailbox permissions",
        epilog=_vocabulary(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mailboxes",
        required=True,
        type=Path,
        help="a JSON file naming each mailbox and its servers; it holds no password",
    )
    parser.add_argument(
        "--permissions",
        required=True,
        type=Path,
        help="the directory holding mailboxes/, which this service only reads",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        help="bind this path; omit it when systemd passes the socket instead",
    )
    parser.add_argument(
        "--media-dir",
        type=Path,
        help="where downloaded attachments land; downloads are refused without it",
    )
    return parser


def _vocabulary():
    """The permission words, printed from the tables that decide them."""
    lines = ["message permissions, decided by any second party of the message:"]
    lines += [f"  {name:<12}{text}" for name, text in verbs.MESSAGE_ACTIONS.items()]
    lines += ["", "recipient permissions, decided by every recipient:"]
    lines += [f"  {name:<12}{text}" for name, text in verbs.RECIPIENT_ACTIONS.items()]
    lines += ["", "mailbox permissions:"]
    lines += [f"  {name:<12}{text}" for name, text in verbs.MAILBOX_ACTIONS.items()]
    lines += [
        "",
        f"  {verbs.WILDCARD:<12}every word the line can grant; every answer says when it was used",
    ]
    return "\n".join(lines)


def _stop(text):
    print(f"mail-agentd: {text}", file=sys.stderr)
    raise SystemExit(EX_CONFIG)


def _password(root, kind, name):
    try:
        return (root / f"{kind}-password-{name}").read_text().strip()
    except FileNotFoundError:
        _stop(f"no {kind} password for mailbox {name} in the credentials directory")


def mailboxes(path):
    """Every configured mailbox, with its passwords read from systemd's credentials.

    The passwords never reach an argument or the environment of a child process: the
    directory is readable only inside this unit's namespace.
    """
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        _stop("no CREDENTIALS_DIRECTORY, so there are no passwords")
    root = Path(directory)
    try:
        declared = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as broken:
        _stop(f"{path}: {broken}")
    context = ssl.create_default_context()

    def imap_connect(host, port):
        from imapclient import IMAPClient

        return IMAPClient(host, port, ssl=True, ssl_context=context, timeout=TIMEOUT)

    def smtp_connect(host, port):
        return smtplib.SMTP_SSL(host, port, context=context, timeout=TIMEOUT)

    boxes = {}
    for name, fields in declared.items():
        try:
            permissions.as_name(name)
            config = client.MailboxConfig.from_dict(name, fields)
            for rule in [config.address, *config.own]:
                addresses.parse_rule(rule)
        except (permissions.BadName, addresses.RuleError, TypeError) as broken:
            _stop(f"mailbox {name}: {broken}")
        boxes[name] = client.Mailbox(
            config,
            _password(root, "imap", name),
            _password(root, "smtp", name),
            imap_connect,
            smtp_connect,
        )
    return boxes


async def run(options):
    boxes = mailboxes(options.mailboxes)
    store = permissions.Store(options.permissions)
    outbox = media.Outbox(options.media_dir) if options.media_dir else None
    served = server.Server(handler.Handler(store, boxes, outbox=outbox, hub=hub.Hub()))
    inherited = server.inherited_socket()
    try:
        if inherited is not None:
            await served.serve_forever(sock=inherited)
        elif options.socket:
            await served.serve_forever(path=str(options.socket))
        else:
            _stop("no socket was passed and none was named")
    finally:
        for box in boxes.values():
            box.close()


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
