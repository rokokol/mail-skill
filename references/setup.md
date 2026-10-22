# What the service needs

`mail-agentd` is meant to run as a systemd unit. That is not a packaging preference: it is where the passwords live. A unit with `DynamicUser=yes` reads its credentials into a namespace no other process shares, and the agent reaches the mailboxes through the socket and never through a password, because no password is visible to it

## What the unit provides

- **A mailbox file**, given with `--mailboxes`: each mailbox's name, address, login, servers, special folders and own addresses. Nothing in it is secret
- **Credentials**, as files named `imap-password-NAME` and `smtp-password-NAME` in `$CREDENTIALS_DIRECTORY`. The service reads them there and nowhere else, so a password never reaches an argument list or a child process's environment. A mailbox whose password is missing stops the service with exit 78, and the unit does not restart it, because a restart cannot supply a password
- **A socket**, either passed by systemd or named with `--socket`. A passed socket is the one to prefer: its owner and mode are the unit's to set
- **A permissions directory**, given with `--permissions`, which the service only reads
- **An outbox**, given with `--media-dir`, which is the only place a download may land

## A private mailbox file

The NixOS module writes the mailbox file from its `mailboxes` option, and that file lands in the Nix store, readable by every user and published with any public configuration. A configuration whose addresses are not to be published gives `configFile` instead: one JSON file, a secret, passed to the service as a credential with the passwords inside it

```json
{
  "work": {
    "address": "you@example.org",
    "imap_host": "mail.example.org",
    "password": "…",
    "own": ["/.*@example\\.org/"]
  }
}
```

Each key is a mailbox name. `address`, `imap_host` and `password` are required. The login defaults to the address and submission to the IMAP host; `user`, `imap_port`, `smtp_host`, `smtp_port`, `folders`, `append_sent` and `own` override them. A key the service does not know stops it with exit 78, so a misspelt one is never ignored

`own` lists the mailbox's own addresses besides its address, literals or /regex/. On a domain of your own, one pattern for the whole domain covers every alias, those added later included. It is wrong for a shared domain: on a public provider's domain it would make every other user an own address

## What a server must offer

- **TLS from the first byte**, on IMAP and on submission. The certificate is checked against the system's trust, with no option to turn that off
- **IDLE**, for a watch. A server without it gets no polling in its place: a subscription is refused with the reason, so nobody waits on mail that is never pushed
- **UIDPLUS**, for `expunge` and for a move where MOVE is missing. Without it the service refuses rather than issue a plain EXPUNGE, which would also remove what other clients marked deleted
- **Special-use flags** on the sent, drafts and trash folders, or a configured name for each. A folder is never guessed from its name

## Passwords and providers

The login is a password, the same for IMAP and SMTP. A provider that requires OAuth for IMAP — Microsoft's, for one — is out of reach. Gmail takes an app password, and files a copy of what is sent by itself, so its mailbox wants `appendSent = false`

Each mailbox holds two IMAP sessions while the service runs: one waiting in IDLE, one serving requests. Providers that count sessions count both
