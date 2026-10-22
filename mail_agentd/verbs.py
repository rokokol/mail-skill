"""The actions a permission can grant, and what each one means.

This module is the single source of truth for the vocabulary. The help text prints it.
The permission parser validates against it. The dispatcher looks each request up here.
A word outside these tables is a refusal, never a silent pass.
"""

# Decided per message. A message is in reach when a rule covers any one of its
# participants: From, To or Cc, the mailbox's own addresses left out
MESSAGE_ACTIONS = {
    "read": "read a message's headers and body, without marking it seen",
    "media": "download a message's attachments into the outbox",
    "mark-read": "set or clear the seen flag, which the user's own clients show",
    "move": "move a message to another folder of the same mailbox",
    "delete": "move a message to Trash, where the user can still find it",
    "expunge": "remove a message for good, which no folder keeps",
}

# Decided per recipient. Every address in To, Cc and Bcc must be covered, because one
# granted address in To would otherwise carry any number of others in Bcc
RECIPIENT_ACTIONS = {
    "send": "send a new message, attachments included",
    "reply": "answer a message, which also needs read on the message answered",
    "forward": "send a message on to other addresses, which also needs read on it",
    "draft": "put a message into Drafts, where the user may send it without reading it",
}

# Decided per mailbox. These words take no address
MAILBOX_ACTIONS = {
    "list": "list the folders of the mailbox, with their message and unseen counts",
}

ADDRESS_ACTIONS = MESSAGE_ACTIONS | RECIPIENT_ACTIONS
ACTIONS = ADDRESS_ACTIONS | MAILBOX_ACTIONS

# One word for every action in a table. Every caller that reports a decision says when the
# grant came from this word rather than from the action's own name, because the holder
# chose one word and received the whole table
WILDCARD = "all"


def expand(word, table):
    """Return the action names a granted word stands for, sorted."""
    return sorted(table) if word == WILDCARD else [word]


def unknown_words(words, table):
    """Return the given words that the table does not define, in order."""
    return [w for w in words if w != WILDCARD and w not in table]


def granted(action, words, table):
    """Whether these granted words permit this action.

    Returns the word that permitted it, so the caller can report a wildcard as one.
    Returns None when nothing permits it.
    """
    if action in words:
        return action
    if WILDCARD in words and action in table:
        return WILDCARD
    return None
