"""The vocabulary is one table per kind of decision, and a word means only itself.

A narrow word is never a consequence of a broad one: read does not imply media, send does
not imply forward, and delete does not imply expunge. The wildcard is the only word that
stands for more than its own name, and it is reported as such.
"""

from mail_agentd import verbs


def test_the_three_tables_share_no_word():
    message = set(verbs.MESSAGE_ACTIONS)
    recipient = set(verbs.RECIPIENT_ACTIONS)
    mailbox = set(verbs.MAILBOX_ACTIONS)
    assert not message & recipient
    assert not message & mailbox
    assert not recipient & mailbox
    assert set(verbs.ACTIONS) == message | recipient | mailbox


def test_the_wildcard_is_no_action_of_its_own():
    assert verbs.WILDCARD not in verbs.ACTIONS


def test_the_wildcard_expands_to_the_whole_table_and_a_word_to_itself():
    assert verbs.expand("all", verbs.MESSAGE_ACTIONS) == sorted(verbs.MESSAGE_ACTIONS)
    assert verbs.expand("read", verbs.MESSAGE_ACTIONS) == ["read"]


def test_a_word_outside_the_table_is_named():
    assert verbs.unknown_words(["read", "all", "delete-everything"], verbs.ACTIONS) == [
        "delete-everything"
    ]


def test_a_grant_names_the_word_that_allowed_it():
    assert verbs.granted("read", {"read"}, verbs.ACTIONS) == "read"
    assert verbs.granted("read", {"all"}, verbs.ACTIONS) == "all"
    assert verbs.granted("media", {"read"}, verbs.ACTIONS) is None


def test_the_wildcard_grants_only_its_own_table():
    # A line about mail that arrived at an own address takes message words alone, so
    # "all" there must never reach send
    assert verbs.granted("send", {"all"}, verbs.MESSAGE_ACTIONS) is None
    assert verbs.granted("read", {"all"}, verbs.MESSAGE_ACTIONS) == "all"


def test_deleting_and_expunging_are_separate_words():
    assert verbs.granted("expunge", {"delete"}, verbs.ACTIONS) is None
    assert verbs.granted("delete", {"expunge"}, verbs.ACTIONS) is None


def test_sending_never_implies_forwarding_or_drafting():
    for word in ("forward", "draft", "reply"):
        assert verbs.granted(word, {"send"}, verbs.ACTIONS) is None
