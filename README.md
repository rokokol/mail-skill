<div align="center">

# Mail skill

**Your mailboxes, reachable by an agent that cannot widen its own reach**

</div>

Lets an agent read and send mail in your IMAP and SMTP mailboxes, under permissions you write and it cannot. The passwords live inside a systemd unit's namespace, the agent talks to a unix socket, and every action it takes is a word you granted on a mailbox and an address you named
