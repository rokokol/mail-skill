"""Read a mailbox's permission file and decide whether an action is allowed.

The files live in a directory the agent cannot write. The agent proposes a line and the
user applies it; nothing here writes. A missing file is a refusal, not an error, so a
mailbox nobody granted anything on behaves the same as one that does not exist.

A file holds three kinds of grant. `allow:` alone covers every address. `allow RULE:`
covers the second party of a message, an address or a /regex/. `allow to RULE:` covers
mail that reached one of the mailbox's own addresses, from anyone. The mailbox's own
addresses are never a second party, or the address every message is sent to would open
the whole mailbox.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import addresses, verbs

# The name reaches this module from the agent, and a file path is built from it
NAME = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")

TRUE_WORDS = frozenset({"yes", "true", "on", "1"})


class BadName(ValueError):
    """The mailbox name is not one a permission file can carry."""


class PermissionFileError(ValueError):
    """The permission file holds a line, a word or a rule it cannot mean."""


def as_name(value):
    name = str(value)
    if not NAME.match(name):
        raise BadName(f"{name!r} is not a mailbox name")
    return name


@dataclass(frozen=True)
class Decision:
    """Whether an action is allowed, and what decided it.

    `by` is the granted word that permitted it, which differs from the action when the
    wildcard did. `via` is the line that held the word. A refusal carries `remedy`, the
    line that would lift it, and `uncovered`, the addresses no line reached.
    """

    allowed: bool
    by: str | None
    reason: str
    via: str | None = None
    remedy: str | None = None
    uncovered: list = field(default_factory=list)


@dataclass
class Grant:
    """One mailbox's file, parsed. Built fresh for every question."""

    name: str
    path: Path
    exists: bool = False
    words: set = field(default_factory=set)
    rules: list = field(default_factory=list)
    to_rules: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)

    def reads_anything(self):
        """Whether any line of the file grants read, on any address."""
        if verbs.granted("read", self.words, verbs.ACTIONS):
            return True
        return any(verbs.granted("read", w, verbs.ADDRESS_ACTIONS) for _, w in self.rules) or any(
            verbs.granted("read", w, verbs.MESSAGE_ACTIONS) for _, w in self.to_rules
        )

    def may_mailbox(self, action):
        if action not in verbs.MAILBOX_ACTIONS:
            raise ValueError(f"{action!r} is no mailbox action")
        if not self.exists:
            return self._no_file()
        by = verbs.granted(action, self.words, verbs.ACTIONS)
        if by is None:
            return self._refuse(action, f"mailbox {self.name} does not allow {action}", "allow")
        return Decision(True, by, f"mailbox {self.name} allows {action}", via="allow")

    def may_message(self, action, counterparts, reached):
        """Decide an action on one message.

        `counterparts` are its From, To and Cc addresses with the own ones taken out, and
        `reached` the own addresses among them. One covered address is enough.
        """
        if action not in verbs.MESSAGE_ACTIONS:
            raise ValueError(f"{action!r} is no message action")
        if not self.exists:
            return self._no_file()
        by = verbs.granted(action, self.words, verbs.ACTIONS)
        if by is not None:
            return Decision(True, by, f"mailbox {self.name} allows {action}", via="allow")
        try:
            for table, rules, candidates, prefix in (
                (verbs.ADDRESS_ACTIONS, self.rules, counterparts, "allow "),
                (verbs.MESSAGE_ACTIONS, self.to_rules, reached, "allow to "),
            ):
                for rule, words in rules:
                    by = verbs.granted(action, words, table)
                    if by is None:
                        continue
                    for address in candidates:
                        if rule.covers(address):
                            return Decision(
                                True,
                                by,
                                f"mailbox {self.name} allows {action} for {address}",
                                via=prefix + rule.text,
                            )
        except addresses.RuleTimeout as slow:
            return Decision(False, None, f"mailbox {self.name}: {slow}")
        named = ", ".join(counterparts) or "no address"
        first = counterparts[0] if counterparts else (reached[0] if reached else None)
        line = f"allow {first}" if counterparts else (f"allow to {first}" if first else "allow")
        return self._refuse(
            action,
            f"mailbox {self.name} does not allow {action} on a message from or to {named}",
            line,
        )

    def may_recipients(self, action, recipients):
        """Decide sending to these addresses. Every one of them must be covered."""
        if action not in verbs.RECIPIENT_ACTIONS:
            raise ValueError(f"{action!r} is no recipient action")
        if not self.exists:
            return self._no_file()
        if not recipients:
            return Decision(False, None, f"{action} names no recipient")
        by = verbs.granted(action, self.words, verbs.ACTIONS)
        if by is not None:
            return Decision(True, by, f"mailbox {self.name} allows {action}", via="allow")
        uncovered = []
        used = set()
        try:
            for address in recipients:
                found = None
                for rule, words in self.rules:
                    word = verbs.granted(action, words, verbs.ADDRESS_ACTIONS)
                    if word is not None and rule.covers(address):
                        found = word
                        break
                if found is None:
                    uncovered.append(address)
                else:
                    used.add(found)
        except addresses.RuleTimeout as slow:
            return Decision(False, None, f"mailbox {self.name}: {slow}")
        if uncovered:
            remedy = "; ".join(
                f"add {action} to 'allow {address}:' in mailboxes/{self.name}.conf"
                for address in uncovered
            )
            return Decision(
                False,
                None,
                f"mailbox {self.name} does not allow {action} to {', '.join(uncovered)}",
                remedy=remedy,
                uncovered=uncovered,
            )
        # A send covered partly by the wildcard is reported as the wildcard, since that is
        # the word that stood for more than its own name
        by = verbs.WILDCARD if verbs.WILDCARD in used else action
        return Decision(True, by, f"mailbox {self.name} allows {action} to every recipient")

    def _no_file(self):
        return Decision(False, None, f"no permission file for mailbox {self.name}")

    def _refuse(self, action, reason, line):
        return Decision(
            False,
            None,
            reason,
            remedy=f"add {action} to '{line}:' in mailboxes/{self.name}.conf",
        )


class Store:
    """The permission files, read fresh on every question.

    Nothing is cached. The user edits these files between requests, and a cached grant
    would outlive a permission they withdrew.
    """

    def __init__(self, root):
        self.root = Path(root)

    def grant(self, name):
        name = as_name(name)
        path = self.root / "mailboxes" / f"{name}.conf"
        grant = Grant(name, path)
        try:
            text = path.read_text()
        except FileNotFoundError:
            return grant
        grant.exists = True
        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # A word never holds a colon and a regex may, so the last colon divides
            key, separator, value = line.rpartition(":")
            if not separator:
                raise PermissionFileError(f"{path}:{number}: line is not key: value: {line!r}")
            key, value = key.strip(), value.strip()
            if key == "allow":
                grant.words |= self._words(path, number, value, verbs.ACTIONS)
            elif key.startswith("allow to "):
                rule = self._rule(path, number, key.removeprefix("allow to "))
                words = self._words(path, number, value, verbs.MESSAGE_ACTIONS)
                grant.to_rules.append((rule, words))
            elif key.startswith("allow "):
                rule = self._rule(path, number, key.removeprefix("allow "))
                words = self._words(path, number, value, verbs.ADDRESS_ACTIONS)
                grant.rules.append((rule, words))
            else:
                grant.flags[key] = value
        return grant

    def mailbox_flag(self, name, flag):
        """Read a boolean setting from a mailbox's file, false when absent."""
        return self.grant(name).flags.get(flag, "").strip().lower() in TRUE_WORDS

    def survey(self):
        """Every mailbox that holds a grant, with what each line expands to.

        A wildcard is listed expanded, so that this answer shows the reach it carries. A
        file that cannot be read is reported in place of its grants, because hiding it
        would make a broken grant look like a missing one.
        """
        held = {}
        directory = self.root / "mailboxes"
        if directory.is_dir():
            for path in sorted(directory.glob("*.conf")):
                try:
                    grant = self.grant(path.stem)
                except (PermissionFileError, BadName) as broken:
                    held[path.stem] = {"error": str(broken)}
                    continue
                held[path.stem] = {
                    "allow": _expanded(grant.words, verbs.ACTIONS),
                    "rules": [_rule_entry(r, w, verbs.ADDRESS_ACTIONS) for r, w in grant.rules],
                    "to": [_rule_entry(r, w, verbs.MESSAGE_ACTIONS) for r, w in grant.to_rules],
                }
        return {"mailboxes": held}

    def _words(self, path, number, value, table):
        words = {w.strip() for w in value.split(",") if w.strip()}
        unknown = verbs.unknown_words(sorted(words), table)
        if unknown:
            raise PermissionFileError(
                f"{path}:{number}: {', '.join(unknown)} is no action this line can grant"
            )
        return words

    def _rule(self, path, number, text):
        try:
            return addresses.parse_rule(text)
        except addresses.RuleError as broken:
            raise PermissionFileError(f"{path}:{number}: {broken}") from broken


def _expanded(words, table):
    actions = set()
    for word in words:
        actions.update(verbs.expand(word, table))
    return sorted(actions)


def _rule_entry(rule, words, table):
    kind = "regex" if rule.text.startswith("/") else "address"
    text = rule.text[1:-1] if kind == "regex" else addresses.canonical(rule.text)
    return {kind: text, "allow": _expanded(words, table)}
