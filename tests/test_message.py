"""A message is data: its addresses come from the raw headers, its body becomes text.

Encoded words are decoded only for display. Were the addresses read after decoding, a
display name could carry an address of its own past every rule. HTML becomes text and
nothing remote is fetched. Authentication results are reported, never trusted.
"""

from email.message import EmailMessage

from mail_agentd import addresses, message


def build(**fields):
    msg = EmailMessage()
    body = fields.pop("body", "hello")
    html = fields.pop("html", None)
    attach = fields.pop("attach", None)
    raw_headers = fields.pop("raw_headers", [])
    for name, value in fields.items():
        msg[name.replace("_", "-")] = value
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if attach:
        msg.add_attachment(attach[1], maintype="application", subtype="pdf", filename=attach[0])
    raw = msg.as_bytes()
    for header in raw_headers:
        raw = header.encode() + b"\r\n" + raw
    return raw


def test_an_encoded_word_cannot_become_an_address():
    # An encoded word where an address stands decodes into one: boss@corp.com, which the
    # sender never had to own. Read raw, it holds no @ and names nobody
    raw = (
        b"From: =?utf-8?q?boss=40corp=2Ecom?=, <evil@x.org>\r\n"
        b"To: work@example.org\r\nSubject: hi\r\n\r\nbody\r\n"
    )
    parsed = message.parse(raw)
    assert message.participants(parsed) == ["evil@x.org", "work@example.org"]


def test_participants_are_from_to_and_cc_but_not_reply_to_or_bcc():
    raw = build(
        From="a@x.org",
        To="b@x.org",
        Cc="c@x.org",
        Reply_To="d@x.org",
        raw_headers=["Bcc: e@x.org"],
    )
    assert message.participants(message.parse(raw)) == ["a@x.org", "b@x.org", "c@x.org"]


def test_own_addresses_are_split_out_by_the_rules():
    own = [addresses.parse_rule("work@example.org"), addresses.parse_rule(r"/gh-.*@example\.org/")]
    counterparts, reached = message.split_own(
        ["a@x.org", "gh-git@example.org", "alice@example.org", "work@example.org"], own
    )
    assert counterparts == ["a@x.org", "alice@example.org"]
    assert reached == ["gh-git@example.org", "work@example.org"]


def test_the_reply_address_is_the_first_own_address_in_to_then_cc():
    own = [addresses.parse_rule(r"/.*@example\.org/")]
    raw = build(From="a@x.org", To="b@x.org, github@example.org", Cc="work@example.org")
    assert message.own_reached(message.parse(raw), own) == "github@example.org"
    assert message.own_reached(message.parse(build(From="a@x.org", To="b@x.org")), own) is None


def test_html_becomes_text_without_its_images():
    raw = build(
        From="a@x.org",
        To="b@x.org",
        body="",
        html='<p>Hello <b>you</b></p><img src="https://track.example/pixel.gif">',
    )
    text = message.describe(raw, full=True)["text"]
    assert "Hello" in text and "track.example" not in text


def test_plain_text_is_preferred_over_html():
    raw = build(From="a@x.org", To="b@x.org", body="the plain one", html="<p>the html one</p>")
    assert "the plain one" in message.describe(raw, full=True)["text"]


def test_a_summary_holds_no_body():
    raw = build(From="a@x.org", To="b@x.org", Subject="s", body="secret body")
    summary = message.describe(raw, full=False)
    assert "text" not in summary and summary["subject"] == "s"


def test_attachments_are_listed_by_name_and_size():
    raw = build(From="a@x.org", To="b@x.org", attach=("report.pdf", b"%PDF" * 10))
    described = message.describe(raw, full=True)
    assert described["attachments"] == [
        {"index": 0, "name": "report.pdf", "type": "application/pdf", "size": 40}
    ]


def test_authentication_results_are_reported_from_the_top_only():
    raw = build(
        From="a@x.org",
        To="b@x.org",
        raw_headers=[
            "Authentication-Results: forged; dkim=pass",
            "Authentication-Results: mail.example.org; dkim=fail",
        ],
    )
    assert message.describe(raw, full=False)["authentication"] == "mail.example.org; dkim=fail"


def test_several_from_headers_are_named_in_a_warning():
    raw = build(From="a@x.org", To="b@x.org", raw_headers=["From: c@x.org"])
    described = message.describe(raw, full=False)
    assert any("From" in w for w in described["warnings"])
    assert message.participants(message.parse(raw))[:2] == ["c@x.org", "a@x.org"]


def test_display_names_are_decoded_for_reading():
    raw = b"From: =?utf-8?b?0JjQu9GM0Y8=?= <a@x.org>\r\nTo: b@x.org\r\n\r\nx\r\n"
    assert message.describe(raw, full=False)["from"] == [{"name": "Илья", "address": "a@x.org"}]
