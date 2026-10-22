"""A message as data: its participants, its own addresses, and a readable body.

The addresses that decisions rest on are read from the raw headers, before any encoded
word is decoded. A decoded display name is text the sender wrote, and parsed again it can
hold an address of its own. Decoding happens only for what is shown.

Nothing here follows a link or loads an image. HTML is turned into text, and the result
is data like any other part of the message: it may read like an instruction, and it is
still only what someone wrote.
"""

import email
import email.errors
import email.header
import email.policy
import email.utils

import html2text

from . import addresses

PARTICIPANT_HEADERS = ("From", "To", "Cc")


def parse(raw):
    """The message with its headers exactly as they arrived."""
    return email.message_from_bytes(raw, policy=email.policy.compat32)


def participants(parsed):
    """The canonical addresses in From, To and Cc, in that order, each once."""
    seen = []
    for header in PARTICIPANT_HEADERS:
        for address in addresses.addresses_in(parsed.get_all(header, [])):
            if address not in seen:
                seen.append(address)
    return seen


def split_own(found, own_rules):
    """Divide addresses into the second parties and the mailbox's own."""
    counterparts, reached = [], []
    for address in found:
        (reached if any(rule.covers(address) for rule in own_rules) else counterparts).append(
            address
        )
    return counterparts, reached


def own_reached(parsed, own_rules):
    """The own address a reply should come from: the first one in To, then in Cc."""
    for header in ("To", "Cc"):
        for address in addresses.addresses_in(parsed.get_all(header, [])):
            if any(rule.covers(address) for rule in own_rules):
                return address
    return None


def _decoded(value):
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except (UnicodeError, LookupError, email.errors.HeaderParseError):
        return value


def _people(parsed, header):
    return [
        {"name": _decoded(name), "address": addresses.canonical(address)}
        for name, address in email.utils.getaddresses(parsed.get_all(header, []))
        if address and "@" in address
    ]


def _text(raw):
    rich = email.message_from_bytes(raw, policy=email.policy.default)
    # A plain part left empty beside the HTML one is common, and it is no text to show
    plain = rich.get_body(preferencelist=("plain",))
    if plain is not None and plain.get_content().strip():
        return plain.get_content()
    part = rich.get_body(preferencelist=("html",))
    if part is None:
        return ""
    content = part.get_content()
    converter = html2text.HTML2Text()
    converter.ignore_images = True
    converter.body_width = 0
    return converter.handle(content).strip()


def attachments(raw):
    """The attached parts in order, each with the index a download names it by."""
    rich = email.message_from_bytes(raw, policy=email.policy.default)
    return list(rich.iter_attachments())


def describe(raw, *, full, identity=None):
    """What a reader is shown of one message. A summary leaves the body out."""
    parsed = parse(raw)
    warnings = []
    if len(parsed.get_all("From", [])) > 1:
        warnings.append("the message carries several From headers; all of them count")
    described = {
        **(identity or {}),
        "message_id": parsed.get("Message-ID", "").strip() or None,
        "date": parsed.get("Date"),
        "subject": _decoded(parsed.get("Subject", "")),
        "from": _people(parsed, "From"),
        "to": _people(parsed, "To"),
        "cc": _people(parsed, "Cc"),
        "reply_to": _people(parsed, "Reply-To"),
        # The receiving server prepends its own result, so only the topmost is its word.
        # Anything below came with the message. It is reported, and no grant rests on it
        "authentication": (parsed.get_all("Authentication-Results") or [None])[0],
        "warnings": warnings,
    }
    # A summary is built from the header block alone, which names no attachment
    if full:
        described["text"] = _text(raw)
        described["attachments"] = [
            {
                "index": index,
                "name": part.get_filename(),
                "type": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
            }
            for index, part in enumerate(attachments(raw))
        ]
    return described
