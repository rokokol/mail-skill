# Changelog

Kept in the shape of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), dated rather than numbered, and with no `Unreleased` section — a skill is read at whatever revision you have checked out, so whatever is on the default branch is what everyone already has, and a section for work that has landed but not shipped would never close. The rule lives in the [versioning](https://github.com/rokokol/versioning-skill) skill, which owns what has no version

## 2026-10-22

### Added

- `mail-agentd`, a service on a unix socket that holds the passwords of several mailboxes and reaches them over IMAP and SMTP with TLS, under one permission file per mailbox the agent cannot write
- The words a permission file can grant: `read`, `media`, `mark-read`, `move`, `delete` and `expunge` on a message, `send`, `reply`, `forward` and `draft` on its recipients, `list` on the mailbox, and `all` for a whole table
- Grants by address: `allow ADDRESS:` or `allow /REGEX/:` for the other party of a message, and `allow to ADDRESS:` for mail that reached one of the mailbox's own addresses. A message is in reach when any participant is covered, and a send only when every recipient is, Bcc included
- `ownAddresses` on a mailbox: its aliases, as literals or regexes. They never count as the other party, and a reply comes from the own address the message reached
- `mail.py`, the client: `permissions`, `mailboxes`, `list`, `read`, `media`, `send`, `reply`, `forward`, `draft`, `mark-read`, `move`, `delete` and `expunge`
- `subscribe` and `mail-watch.py`: each new message the grant reaches, pushed as the server announces it over IMAP IDLE, with a catch-up from the last message seen after any gap
- A NixOS module that runs the service under `DynamicUser` with the passwords as credentials
