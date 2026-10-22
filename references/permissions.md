# The permission files

One file per mailbox, in a directory the service reads and the agent cannot write. The agent proposes a line; the user applies it. That asymmetry is the whole design: a permission the agent can edit is not a permission

```
<permissions>/mailboxes/work.conf
```

The name is the mailbox's name from `mail.py mailboxes`: lower-case letters, digits and hyphens. A name of any other shape is refused before it becomes a path

## The format

Plain lines. A `#` opens a comment, blank lines are ignored. The words after the last colon are the grant, separated by commas, and `mail-agentd --help` prints every word with what it permits

```
allow: list
allow boss@corp.com: read, reply, media
allow /.*@corp\.com/: read
allow to github@example.org: read
mark-read-on-reply: yes
```

- **`allow:`** grants its words for every address
- **`allow RULE:`** grants its words for one second party: a literal address, or a regex between slashes. A regex matches the whole address, ignoring case, so `/.*@corp\.com/` never covers `boss@corp.com.attacker.net`. A regex may hold a colon, since the last colon on the line is the one that divides
- **`allow to RULE:`** grants message words for mail that reached one of the mailbox's own addresses, from anyone: everything that came to a service's alias, say. It never grants sending
- **`mark-read-on-reply`** makes a reply mark the message read

A word outside the vocabulary, or a regex that does not compile, is an error naming the file and the line, never a silent pass. A regex runs on addresses strangers write, and a match that takes too long ends in a refusal rather than a stalled service

## Who a rule covers

Every address is compared in one form: lower case, Unicode normalised, a punycode domain read as its letters. The display name never takes part, since the sender typed it

- **A message** is in reach when a rule covers any one of its From, To and Cc addresses, leaving out the mailbox's own. Reply-To and Bcc do not count
- **A send, a reply, a forward and a draft** are allowed only when a rule covers every recipient: To, Cc and Bcc alike. One granted address in To would otherwise carry any number of others in Bcc
- **A reply** is checked against the addresses it will reach: the message's Reply-To, else its From, and with `--all` its other participants, the mailbox's own addresses left out. It needs `read` on the message it answers, and so does a forward

The consequence of "any participant" is worth stating: anyone who puts a granted address in Cc makes their message readable. A grant says whose correspondence the agent reads, not who may write to it

## Own addresses

A mailbox's own addresses are its address and whatever its configuration lists in `ownAddresses`, literals or /regex/ in the same grammar. They are never a second party: otherwise the address every message is sent to would open the whole mailbox, and `allow /.*@example\.org/: read` would mean everything. A reply comes from the own address the message reached, so answering mail sent to an alias does not reveal the mailbox behind it

## Why `all` is reported wherever it is used

`all` grants every word the line can grant. It is a different decision from naming an action: the holder chose one word and received a table that includes expunging and forwarding out. So every answer permitted by `all` says so, and a survey lists it expanded

## Turning a refusal into a permission

A refused request names the line that would lift it, one per address no rule covered. Take those lines to the user, with what was being attempted and why. They apply them; the service reads the file fresh on every request, so the next attempt sees the change with nothing restarted
