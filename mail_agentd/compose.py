"""Build an outgoing message from the parts of a request, refusing what a header cannot hold.

Every value that becomes a header is checked for a line break before anything is built. A
CR or LF inside a subject ends that header and starts another, so an agent could write a
Bcc or a From of its own past every grant.

Bcc never becomes a header. It is part of the envelope alone, which is where a recipient
nobody else may see belongs: a header would reach every recipient and the copy in Sent.
"""

import base64
import binascii
import email.policy
import email.utils
import mimetypes
from email.message import EmailMessage

from . import addresses

FORBIDDEN = ("\r", "\n", "\0")


class BadMessage(ValueError):
    """A part of the request cannot become a message."""


def checked(value, what):
    value = "" if value is None else str(value)
    if any(c in value for c in FORBIDDEN):
        raise BadMessage(f"{what} holds a line break or a NUL, which would end the header")
    return value


def bare_address(value, what):
    """One address with nothing around it, canonical. A list or a display name is refused."""
    text = checked(value, what).strip()
    found = addresses.addresses_in([text])
    if len(found) != 1 or addresses.canonical(text) != found[0]:
        raise BadMessage(f"{what} {text!r} is not one bare address")
    return found[0]


def recipients(request):
    """The To, Cc and Bcc addresses of a request, each a list of bare addresses."""
    lists = {}
    for field in ("to", "cc", "bcc"):
        given = request.get(field) or []
        if isinstance(given, str):
            given = [given]
        lists[field] = [bare_address(value, field) for value in given]
    return lists


def files(request):
    """The attachments of a request as (name, bytes), decoded from base64."""
    attached = []
    for item in request.get("files") or []:
        try:
            name = checked(item["name"], "a file name")
            data = base64.b64decode(item["data"], validate=True)
        except (KeyError, TypeError, binascii.Error) as broken:
            raise BadMessage(f"a file is {{name, data}} with data in base64: {broken}") from None
        attached.append((name, data))
    return attached


def build(*, sender, to, cc, subject, text, html=None, attached=(), in_reply_to=None, references=None, extra=None):
    """The message, with a fresh Message-ID in the sender's domain. Bcc is not an argument."""
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = sender
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = checked(subject, "subject")
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=sender.rpartition("@")[2])
    if in_reply_to:
        msg["In-Reply-To"] = checked(in_reply_to, "In-Reply-To")
    if references:
        msg["References"] = checked(references, "References")
    msg.set_content(checked_body(text))
    if html:
        msg.add_alternative(checked_body(html), subtype="html")
    for name, data in attached:
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        maintype, _, subtype = kind.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    for part in extra or ():
        msg.add_attachment(part)
    return msg


def checked_body(text):
    # A body may hold line breaks; only NUL is never valid text
    text = "" if text is None else str(text)
    if "\0" in text:
        raise BadMessage("the body holds a NUL")
    return text


def as_bytes(msg):
    return msg.as_bytes(policy=email.policy.SMTP)
