"""A decision rests on the address itself, in one canonical form, matched whole.

The display name is whatever the sender typed, so it never takes part. A rule is a literal
address or a regex between slashes, and a regex must match the whole address: a search
would let boss@corp.com.attacker.net through a rule written for corp.com. The sender also
controls the string a regex runs on, so a slow pattern ends in a refusal, not in a stalled
service.
"""

import time

import pytest

from mail_agentd import addresses


def test_canonical_form_lowers_both_halves():
    assert addresses.canonical("Boss@Corp.COM") == "boss@corp.com"


def test_canonical_form_reads_a_punycode_domain_as_its_letters():
    assert addresses.canonical("ilya@XN--80ATJC.xn--p1ai") == "ilya@окна.рф"


def test_canonical_form_keeps_a_domain_that_is_not_valid_punycode():
    assert addresses.canonical("a@XN--A.org") == "a@xn--a.org"


def test_the_display_name_never_becomes_the_address():
    encoded = "=?utf-8?q?boss=40corp.com?= <evil@x.org>"
    quoted = '"boss@corp.com" <evil@x.org>'
    assert addresses.addresses_in([encoded]) == ["evil@x.org"]
    assert addresses.addresses_in([quoted]) == ["evil@x.org"]


def test_every_header_and_every_address_in_it_is_read():
    assert addresses.addresses_in(["a@x.org, B@y.org", "c@z.org"]) == [
        "a@x.org",
        "b@y.org",
        "c@z.org",
    ]


def test_an_unparsable_header_yields_no_address():
    assert addresses.addresses_in(["<<<not an address"]) == []


def test_a_literal_rule_matches_the_canonical_address_only():
    rule = addresses.parse_rule("Boss@Corp.com")
    assert rule.covers("boss@corp.com")
    assert not rule.covers("boss@corp.com.attacker.net")
    assert not rule.covers("xboss@corp.com")


def test_a_regex_rule_matches_the_whole_address():
    rule = addresses.parse_rule(r"/.*@corp\.com/")
    assert rule.covers("anyone@corp.com")
    assert not rule.covers("anyone@corp.com.attacker.net")
    assert not rule.covers("x@corp.com@y.org")


def test_a_regex_rule_ignores_case():
    assert addresses.parse_rule(r"/BOSS@corp\.com/").covers("boss@corp.com")


def test_a_regex_rule_reads_letters_in_a_domain():
    assert addresses.parse_rule(r"/.*@окна\.рф/").covers(addresses.canonical("a@xn--80atjc.xn--p1ai"))


def test_a_broken_regex_is_an_error_naming_the_pattern():
    with pytest.raises(addresses.RuleError, match=r"\(unclosed"):
        addresses.parse_rule("/(unclosed/")


def test_a_rule_that_is_no_address_is_an_error():
    with pytest.raises(addresses.RuleError):
        addresses.parse_rule("not an address")


def test_a_slow_regex_on_a_hostile_address_ends_in_an_error():
    # The regex module defuses the textbook (a+)+, so the alternation is what still explodes
    rule = addresses.parse_rule("/(a|aa)+@x/")
    started = time.monotonic()
    with pytest.raises(addresses.RuleTimeout):
        rule.covers("a" * 40 + "!")
    assert time.monotonic() - started < 2
