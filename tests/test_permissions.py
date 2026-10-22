"""A mailbox's permission file decides, and a refusal names the line that would lift it.

A message is in reach when one rule covers any of its counterparts, or when a "to" rule
covers an own address it reached. A send is allowed only when every recipient is covered.
The file is read fresh on every question, and a word or a rule it cannot read is an error
naming the file, never a silent pass.
"""

import pytest

from mail_agentd import permissions


@pytest.fixture
def store(tmp_path):
    (tmp_path / "mailboxes").mkdir()

    def write(name, text):
        (tmp_path / "mailboxes" / f"{name}.conf").write_text(text)

    s = permissions.Store(tmp_path)
    s.write = write
    return s


def test_a_mailbox_with_no_file_allows_nothing(store):
    decision = store.grant("work").may_message("read", ["a@x.org"], [])
    assert not decision.allowed
    assert "no permission file" in decision.reason


def test_a_name_that_is_not_a_mailbox_name_never_becomes_a_path(store):
    for name in ("../etc", "Work", "a/b", "", "-x"):
        with pytest.raises(permissions.BadName):
            store.grant(name)


def test_a_mailbox_wide_word_covers_every_address(store):
    store.write("work", "allow: read\n")
    decision = store.grant("work").may_message("read", ["anyone@x.org"], [])
    assert decision.allowed and decision.by == "read"


def test_a_message_is_in_reach_when_any_counterpart_is_covered(store):
    store.write("work", "allow boss@corp.com: read\n")
    grant = store.grant("work")
    assert grant.may_message("read", ["stranger@x.org", "boss@corp.com"], []).allowed
    refused = grant.may_message("read", ["stranger@x.org"], [])
    assert not refused.allowed
    assert "allow stranger@x.org:" in refused.remedy


def test_a_counterpart_rule_never_reaches_the_own_addresses(store):
    store.write("work", r"allow /.*@example\.org/: read" + "\n")
    # The mail reached github@example.org, an own address, from a stranger: the rule names
    # the second party, and there is no second party at example.org here
    refused = store.grant("work").may_message("read", ["stranger@x.org"], ["github@example.org"])
    assert not refused.allowed


def test_a_to_rule_covers_mail_that_reached_that_own_address(store):
    store.write("work", "allow to github@example.org: read\n")
    grant = store.grant("work")
    assert grant.may_message("read", ["stranger@x.org"], ["github@example.org"]).allowed
    assert not grant.may_message("read", ["stranger@x.org"], ["work@example.org"]).allowed


def test_a_to_rule_grants_message_words_only(store):
    store.write("work", "allow to github@example.org: send\n")
    with pytest.raises(permissions.PermissionFileError, match="work.conf"):
        store.grant("work")


def test_a_to_rule_with_the_wildcard_never_reaches_send(store):
    store.write("work", "allow to github@example.org: all\n")
    assert not store.grant("work").may_recipients("send", ["github@example.org"]).allowed


def test_every_recipient_must_be_covered(store):
    store.write("work", "allow boss@corp.com: send\n")
    grant = store.grant("work")
    assert grant.may_recipients("send", ["boss@corp.com"]).allowed
    refused = grant.may_recipients("send", ["boss@corp.com", "hidden@x.org"])
    assert not refused.allowed
    assert refused.uncovered == ["hidden@x.org"]
    assert "allow hidden@x.org:" in refused.remedy


def test_no_recipient_at_all_is_a_refusal(store):
    store.write("work", "allow: send\n")
    assert not store.grant("work").may_recipients("send", []).allowed


def test_a_regex_rule_between_slashes_matches_whole_addresses(store):
    store.write("work", r"allow /.*@corp\.com/: send" + "\n")
    grant = store.grant("work")
    assert grant.may_recipients("send", ["a@corp.com", "b@corp.com"]).allowed
    assert not grant.may_recipients("send", ["a@corp.com.evil.net"]).allowed


def test_a_regex_may_hold_a_colon(store):
    store.write("work", r"allow /a:b@x\.org|c@x\.org/: read" + "\n")
    assert store.grant("work").may_message("read", ["c@x.org"], []).allowed


def test_a_wildcard_is_reported_as_one(store):
    store.write("work", "allow boss@corp.com: all\n")
    decision = store.grant("work").may_message("expunge", ["boss@corp.com"], [])
    assert decision.allowed and decision.by == "all"


def test_a_mailbox_word_comes_only_from_the_mailbox_wide_line(store):
    store.write("work", "allow boss@corp.com: all\n")
    assert not store.grant("work").may_mailbox("list").allowed
    store.write("work", "allow: list\n")
    assert store.grant("work").may_mailbox("list").allowed


def test_an_unknown_word_is_an_error_naming_the_file(store):
    store.write("work", "allow boss@corp.com: read, raed\n")
    with pytest.raises(permissions.PermissionFileError, match=r"work\.conf.*raed"):
        store.grant("work")


def test_a_broken_rule_is_an_error_naming_the_file(store):
    store.write("work", "allow /(oops/: read\n")
    with pytest.raises(permissions.PermissionFileError, match=r"work\.conf"):
        store.grant("work")


def test_a_slow_rule_refuses_rather_than_stalls(store):
    store.write("work", "allow /(a|aa)+@x/: read\n")
    refused = store.grant("work").may_message("read", ["a" * 33 + "!"], [])
    assert not refused.allowed
    assert "longer than" in refused.reason


def test_the_file_is_read_fresh_on_every_question(store):
    store.write("work", "allow: read\n")
    assert store.grant("work").may_message("read", ["a@x.org"], []).allowed
    store.write("work", "allow: list\n")
    assert not store.grant("work").may_message("read", ["a@x.org"], []).allowed


def test_a_flag_is_false_unless_said(store):
    store.write("work", "allow: read\n")
    assert not store.mailbox_flag("work", "mark-read-on-reply")
    store.write("work", "allow: read\nmark-read-on-reply: yes\n")
    assert store.mailbox_flag("work", "mark-read-on-reply")


def test_the_survey_lists_every_rule_with_its_words_expanded(store):
    store.write("work", "allow: list\nallow boss@corp.com: all\nallow to github@example.org: read\n")
    store.write("broken", "allow: raed\n")
    survey = store.survey()["mailboxes"]
    work = survey["work"]
    assert work["allow"] == ["list"]
    assert work["rules"][0]["address"] == "boss@corp.com"
    assert "expunge" in work["rules"][0]["allow"] and "send" in work["rules"][0]["allow"]
    assert work["to"][0] == {"address": "github@example.org", "allow": ["read"]}
    assert "raed" in survey["broken"]["error"]
