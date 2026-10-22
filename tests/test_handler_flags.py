"""Marking, moving, deleting and expunging are four words, decided per message.

A request naming several messages acts on all of them or on none: one message out of
reach refuses the whole request, so the agent never learns which part went through by
counting what changed. Deleting moves to Trash, where the user can still find it;
expunging is the separate word that destroys.
"""

from tests.conftest import mail
from tests.fake_imap import SEEN


def test_mark_read_needs_its_word_and_sets_the_flag(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: read\n")
    assert not world.ask(action="mark-read", mailbox="work", uids=[uid])["ok"]
    world.grant("work", "allow boss@corp.com: mark-read\n")
    assert world.ask(action="mark-read", mailbox="work", uids=[uid])["ok"]
    assert SEEN in server.folders["INBOX"].messages[uid]["flags"]
    assert world.ask(action="mark-read", mailbox="work", uids=[uid], seen=False)["ok"]
    assert SEEN not in server.folders["INBOX"].messages[uid]["flags"]


def test_one_message_out_of_reach_refuses_the_whole_request(world):
    server = world.mailbox("work", "work@example.org")
    mine = server.deliver(mail("boss@corp.com"))
    other = server.deliver(mail("stranger@x.org"))
    world.grant("work", "allow boss@corp.com: delete\n")
    answer = world.ask(action="delete", mailbox="work", uids=[mine, other])
    assert not answer["ok"] and "allow stranger@x.org:" in answer["remedy"]
    assert set(server.folders["INBOX"].messages) == {mine, other}


def test_delete_moves_to_trash(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: delete\n")
    assert world.ask(action="delete", mailbox="work", uids=[uid])["ok"]
    assert uid not in server.folders["INBOX"].messages
    assert len(server.folders["Trash"].messages) == 1


def test_delete_never_expunges(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: delete\n")
    assert not world.ask(action="expunge", mailbox="work", uids=[uid])["ok"]
    assert uid in server.folders["INBOX"].messages


def test_expunge_destroys_only_what_it_names(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    keep = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: expunge\n")
    assert world.ask(action="expunge", mailbox="work", uids=[uid])["ok"]
    assert set(server.folders["INBOX"].messages) == {keep}
    assert server.folders["Trash"].messages == {}


def test_move_takes_messages_to_the_named_folder(world):
    server = world.mailbox("work", "work@example.org")
    server.folder("Archive")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: move\n")
    answer = world.ask(action="move", mailbox="work", uids=[uid], to_folder="Archive")
    assert answer["ok"], answer
    assert len(server.folders["Archive"].messages) == 1


def test_a_move_names_its_target(world):
    server = world.mailbox("work", "work@example.org")
    uid = server.deliver(mail("boss@corp.com"))
    world.grant("work", "allow boss@corp.com: move\n")
    assert not world.ask(action="move", mailbox="work", uids=[uid])["ok"]


def test_a_missing_message_is_named(world):
    world.mailbox("work", "work@example.org")
    world.grant("work", "allow: delete\n")
    answer = world.ask(action="delete", mailbox="work", uids=[99])
    assert not answer["ok"] and "99" in answer["error"]
