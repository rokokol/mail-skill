"""Addresses as decisions see them: parsed out of headers, canonical, and matched whole.

Every comparison here runs on the addr-spec alone. The display name is text the sender
chose, and it can spell any address it likes. Both sides of a comparison go through the
same canonical form, so a rule and a header that name one mailbox agree however each was
written.
"""

import email.utils
import unicodedata

import regex

# A sender writes the string every regex runs on, and a pattern the user wrote in good faith
# can still take exponential time on the right input. The regex module stops a match at
# this limit, where the standard re module would hold the service until it finished
MATCH_SECONDS = 0.1


class RuleError(ValueError):
    """A rule is neither an address nor a regex that compiles."""


class RuleTimeout(RuntimeError):
    """A regex rule ran out of time on one address."""


def canonical(address):
    """The one form every address is compared in: NFC, lower case, a readable domain.

    A domain in punycode is decoded to its letters, so a rule written as one reads the
    same mailbox as a header written as the other. A label that is not valid punycode
    is kept as it was, lowered, since it names nothing a decoder could agree on.
    """
    local, at, domain = unicodedata.normalize("NFC", address.strip()).rpartition("@")
    if not at:
        return address.strip().lower()
    labels = []
    for label in domain.lower().split("."):
        if label.startswith("xn--"):
            try:
                label = label.encode("ascii").decode("idna")
            except UnicodeError:
                pass
        labels.append(label)
    return f"{local.lower()}@{'.'.join(labels)}"


def addresses_in(values):
    """The canonical addresses in these header values, in order.

    The parser is strict, so a value it cannot read yields an empty address, which is
    left out rather than guessed at.
    """
    found = []
    for _, address in email.utils.getaddresses(list(values)):
        if address and "@" in address:
            found.append(canonical(address))
    return found


class Rule:
    """A literal address, or a regex between slashes matched against the whole address."""

    def __init__(self, text, literal=None, pattern=None):
        self.text = text
        self._literal = literal
        self._pattern = pattern

    def covers(self, address):
        if self._literal is not None:
            return address == self._literal
        try:
            return self._pattern.fullmatch(address, timeout=MATCH_SECONDS) is not None
        except TimeoutError as slow:
            raise RuleTimeout(
                f"rule {self.text} took longer than {MATCH_SECONDS} s on {address}"
            ) from slow

    def __repr__(self):
        return f"Rule({self.text!r})"


def parse_rule(text):
    """Read one rule as a permission file or an option writes it."""
    text = text.strip()
    if len(text) >= 2 and text.startswith("/") and text.endswith("/"):
        source = unicodedata.normalize("NFC", text[1:-1])
        try:
            pattern = regex.compile(source, regex.IGNORECASE)
        except regex.error as broken:
            raise RuleError(f"{text} is not a regex that compiles: {broken}") from broken
        return Rule(text, pattern=pattern)
    local, at, domain = text.rpartition("@")
    if not at or not local or not domain or any(c.isspace() for c in text):
        raise RuleError(f"{text!r} is neither an address nor a /regex/")
    return Rule(text, literal=canonical(text))
