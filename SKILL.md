---
name: mail
description: "Reach the user's mailboxes over IMAP and SMTP through a service that holds the passwords and decides by per-mailbox and per-address permissions the agent cannot grant itself: read and search mail, wait for a new message, answer, send, forward, save a draft, download attachments, move and delete. Use when the request needs the user's own mail rather than an explanation of email, and when a refusal has to be turned into a permission the user applies. Triggers: check my mail, any new email, what did they write, read the email, answer the email, send an email, forward this email, save a draft, wait for the reply, download the attachment, what am I allowed in mail, проверь почту, что пришло на почту, есть новые письма, прочитай письмо, ответь на письмо, напиши письмо, отправь письмо, перешли письмо, сохрани черновик, дождись ответа на почту, скачай вложение, что мне разрешено в почте"
license: MIT
---

# mail

The mailboxes are reached through `mail-agentd`, a service on a unix socket. It holds the passwords, reads the permission files and performs the action; this side sends a request and reads an answer. The agent sees the names of the mailboxes and nothing that would let it log in
