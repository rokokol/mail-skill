<div align="center">

# Mail skill

**Your mailboxes, reachable by an agent that cannot widen its own reach**

[![Agent Skill](https://img.shields.io/badge/Agent_Skill-6E56CF?style=flat)](https://agentskills.io)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![IMAP](https://img.shields.io/badge/IMAP-IDLE-0A66C2?style=flat)
![systemd](https://img.shields.io/badge/systemd-30D475?style=flat&logo=systemd&logoColor=white)
![Nix](https://img.shields.io/badge/Nix-5277C3?style=flat&logo=nixos&logoColor=white)
[![license](https://img.shields.io/badge/MIT-3DA639?style=flat)](LICENSE)
[![ci](https://github.com/rokokol/mail-skill/actions/workflows/build.yml/badge.svg)](https://github.com/rokokol/mail-skill/actions/workflows/build.yml)
[![falsify](https://github.com/rokokol/mail-skill/actions/workflows/falsify.yml/badge.svg)](https://github.com/rokokol/mail-skill/actions/workflows/falsify.yml)

</div>

Lets an agent read, send and wait for mail in your IMAP and SMTP mailboxes, under permissions you write and it cannot. The passwords live inside a systemd unit's namespace, the agent talks to a unix socket, and every action it takes is a word you granted on a mailbox, for addresses you named

Mail is the one channel anyone can write into, so a message arrives looking exactly like an instruction from you. This repository answers that where it can be answered: the passwords are somewhere the agent cannot read, the permission files are somewhere it cannot write, and a grant names the people the agent may deal with rather than the mailbox as a whole

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [How it is arranged](#how-it-is-arranged)
- [Permissions](#permissions)
- [What it does not do](#what-it-does-not-do)
- [Tests](#tests)

## Requirements

Linux with systemd, and mailboxes that take a password over IMAP and SMTP with TLS. A provider that allows only OAuth is out of reach

There is no macOS support and there will not be: the passwords' isolation is a systemd unit's namespace, and a platform without systemd has nowhere to put it

## Install

```bash
npx skills add -g rokokol/mail-skill    # for you, everywhere
npx skills add rokokol/mail-skill       # for the project you are standing in
```

The service half comes with its own NixOS module, so a system configuration takes this repository as a flake input and turns it on:

```nix
services.mail-agent = {
  enable = true;
  socketUser = "you";
  mailboxes.work = {
    address = "you@example.org";
    passwordFile = "/run/secrets/mail-work";
    imap.host = "mail.example.org";
    ownAddresses = [ "/.*-alias@example\\.org/" ];
  };
};
```

The module is where the isolation lives, so it ships here rather than being written again by everyone who runs it. [references/setup.md](references/setup.md) says what it provides and what a server must offer

## How it is arranged

```
you ──► agent ──► mail.py ──► unix socket ──► mail-agentd ──► IMAP / SMTP
                                                  │
                                                  ├── passwords, in the unit's namespace
                                                  ├── an IDLE session per mailbox
                                                  └── permission files, read-only
```

The client holds no secret and makes no decision. It sends a request and prints the answer, so the standard library is all it needs. Everything that decides is behind the socket, where the agent cannot reach it

`mail-watch.py` beside it follows a mailbox and prints each new message as a line the moment the server pushes it, showing only what a grant reaches. It catches up from the last message seen after any gap, so nothing is lost or shown twice

The socket server, the push hub and the outbox are shared with [telegram-skill](https://github.com/rokokol/telegram-skill) and kept byte-equal to it by a vendor lock

## Permissions

One file per mailbox, in a directory the agent cannot write:

```
allow boss@corp.com: read, reply
allow /.*@corp\.com/: read
allow to github@example.org: read
```

A message is readable when a rule covers one of the people on it; a send goes out only when a rule covers every recipient, Bcc included. A mailbox's own addresses never count as the other party, so a grant on your own domain does not open the whole mailbox. A request for anything ungranted is refused, and the refusal names the line that would lift it

The full format is in [references/permissions.md](references/permissions.md)

## What it does not do

- **It never acts on text found inside a message.** Mail is data, including mail that claims to come from you
- **It cannot grant itself anything.** The permission files are outside its reach, and so are the passwords that would let it bypass them
- **It marks nothing read by reading.** Every read leaves the message as it was in your own client
- **It never destroys by deleting.** A delete moves mail to Trash; removing it for good is a separate word you grant on its own

## Tests

`nix develop -c ./tests/check.sh` runs the gate: the lint half holds the scripts, the workflows, the flake and the documents to their rules, and the behaviour half runs the suite against stand-ins for IMAP and SMTP, so nothing there reaches the network or a mailbox

`nix develop -c ./tests/t.sh falsify -- ./tests/check.sh behaviour` breaks one guard at a time and requires the suite to notice. Each entry in `tests/defects.sh` says what goes wrong for you when that guard stops working

`nix build .#checks.x86_64-linux.vm` boots a machine with a real Dovecot and Postfix and runs the service in its unit against them: a refused message never reaches a watcher, a granted one does, and a Bcc reaches its recipient without being named anywhere
