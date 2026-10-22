#!/usr/bin/env bash
# The defect list for this repository, read by the tests skill's harness vendored beside
# it:
#
#   tests/t.sh falsify -- ./tests/check.sh behaviour
#
# Each entry breaks one guard and requires the suite to notice. The CONSEQUENCE is what
# goes wrong for the user when that guard stops working; when an entry survives, that
# sentence is the report.
#
#   defect NAME FILE FIND REPLACE CONSEQUENCE [expect survived REASON | expect caught FRAGMENT]
#
# A FIND or REPLACE holding a quote is a quoted heredoc, so the line stays exactly as the
# source spells it

# The vocabulary. A narrow word never grants a broad one
defect 'verbs/wildcard-table' 'mail_agentd/verbs.py' \
  '    if WILDCARD in words and action in table:' \
  '    if WILDCARD in words:' \
  'the wildcard on a mailbox grants words from a table it does not belong to'

defect 'verbs/unknown-word' 'mail_agentd/verbs.py' \
  '    return [w for w in words if w != WILDCARD and w not in table]' \
  '    return []' \
  'a misspelt word in a permission file passes in silence, and the user believes a grant holds that does not'

# Addresses. A rule names a mailbox, and nothing the sender writes may stretch it
defect 'address/search' 'mail_agentd/addresses.py' \
  '            return self._pattern.fullmatch(address, timeout=MATCH_SECONDS) is not None' \
  '            return self._pattern.search(address, timeout=MATCH_SECONDS) is not None' \
  'a rule written for corp.com also covers boss@corp.com.attacker.net, so a stranger reads or receives what the rule meant for one company'

defect 'address/display-name' 'mail_agentd/addresses.py' \
  '    for _, address in email.utils.getaddresses(list(values)):' \
  '    for address, _ in email.utils.getaddresses(list(values)):' \
  'a sender who types a granted address as a display name is treated as that address'

defect 'address/case' 'mail_agentd/addresses.py' \
  '    return f"{local.lower()}@{'"'"'.'"'"'.join(labels)}"' \
  '    return f"{local}@{'"'"'.'"'"'.join(labels)}"' \
  'a grant on boss@corp.com stops covering Boss@corp.com, and the agent is refused mail the user granted'

defect 'address/idn' 'mail_agentd/addresses.py' \
  '                label = label.encode("ascii").decode("idna")' \
  '                pass' \
  'a rule written with the letters of a domain does not cover the same domain written in punycode'

defect 'address/regex-timeout' 'mail_agentd/addresses.py' \
  '            return self._pattern.fullmatch(address, timeout=MATCH_SECONDS) is not None' \
  '            return self._pattern.fullmatch(address) is not None' \
  'a crafted From address holds the service in one regex match for minutes, and every other request waits'

defect 'address/literal' 'mail_agentd/addresses.py' \
  '            return address == self._literal' \
  '            return self._literal in address' \
  'a literal rule for boss@corp.com also covers xboss@corp.com and every longer address that contains it'

# Permission files. The agent cannot write them, so what they say is the whole grant
defect 'permission/name' 'mail_agentd/permissions.py' \
  'NAME = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")' \
  'NAME = re.compile(r"\A.+\Z")' \
  'a mailbox named ../x reads a permission file outside the permissions directory'

defect 'permission/every-recipient' 'mail_agentd/permissions.py' \
  '        if uncovered:' \
  '        if len(uncovered) == len(recipients):' \
  'one granted address in To carries any number of strangers in Cc and Bcc'

defect 'permission/no-recipient' 'mail_agentd/permissions.py' \
  '        if not recipients:' \
  '        if False:' \
  'a message with no recipient passes the check, and the server is left to decide where it goes'

defect 'permission/own-address' 'mail_agentd/permissions.py' \
  '                (verbs.ADDRESS_ACTIONS, self.rules, counterparts, "allow "),' \
  '                (verbs.ADDRESS_ACTIONS, self.rules, counterparts + reached, "allow "),' \
  'a rule meant for the second party also matches the address every message is sent to, and opens the whole mailbox'

defect 'permission/to-words' 'mail_agentd/permissions.py' \
  '                words = self._words(path, number, value, verbs.MESSAGE_ACTIONS)' \
  '                words = self._words(path, number, value, verbs.ACTIONS)' \
  'a line about mail that reached an own address accepts send, and the user believes it grants sending to that address'

defect 'permission/unknown-word' 'mail_agentd/permissions.py' \
  '        if unknown:' \
  '        if False:' \
  'a misspelt word in a permission file passes in silence, and the grant the user wrote does not exist'

defect 'permission/timeout' 'mail_agentd/permissions.py' \
  '        except addresses.RuleTimeout as slow:
            return Decision(False, None, f"mailbox {self.name}: {slow}")
        named' \
  '        except addresses.RuleTimeout as slow:
            raise
        named' \
  'a slow rule on a hostile From address crashes the request instead of refusing it'

# Messages. What a decision reads comes from the raw headers, and nothing remote is fetched
defect 'message/decoded-headers' 'mail_agentd/message.py' \
  '    return email.message_from_bytes(raw, policy=email.policy.compat32)' \
  '    return email.message_from_bytes(raw, policy=email.policy.default)' \
  'a display name encoded to spell an address becomes a participant, and a stranger passes as the granted correspondent'

defect 'message/reply-to-participant' 'mail_agentd/message.py' \
  'PARTICIPANT_HEADERS = ("From", "To", "Cc")' \
  'PARTICIPANT_HEADERS = ("From", "To", "Cc", "Reply-To")' \
  'a stranger who names a granted address in Reply-To makes their message readable'

defect 'message/own-split' 'mail_agentd/message.py' \
  '        (reached if any(rule.covers(address) for rule in own_rules) else counterparts).append(' \
  '        counterparts.append(' \
  'the address every message is sent to counts as a second party, and a rule on it opens the whole mailbox'

defect 'message/images' 'mail_agentd/message.py' \
  '    converter.ignore_images = True' \
  '    converter.ignore_images = False' \
  'the text the agent reads carries every tracking pixel as a link it may follow'

defect 'message/auth-top' 'mail_agentd/message.py' \
  '        "authentication": (parsed.get_all("Authentication-Results") or [None])[0],' \
  '        "authentication": (parsed.get_all("Authentication-Results") or [None])[-1],' \
  'the authentication shown is the one the sender wrote into the message, and a forged pass reads as real'

# The session. IMAP defaults mark, renumber and remove more than a request names
defect 'read/peek' 'mail_agentd/client.py' \
  '            fetched = session.fetch([uid], ["BODY.PEEK[]"])' \
  '            fetched = session.fetch([uid], ["BODY[]"])' \
  'every message the agent reads turns read in the user'\''s own mail client, and they miss it'

defect 'read/since' 'mail_agentd/client.py' \
  '            return sorted(u for u in found if after is None or u > int(after))' \
  '            return sorted(found)' \
  'a watcher shows the last message again on every round when nothing new arrived'

defect 'read/uidvalidity' 'mail_agentd/client.py' \
  '        if uidvalidity is not None and int(uidvalidity) != current:' \
  '        if False:' \
  'after a folder is rebuilt, an action on a remembered message lands on a different message'

defect 'expunge/plain' 'mail_agentd/client.py' \
  '        session.uid_expunge(list(uids))' \
  '        session.expunge()' \
  'removing one message also destroys every message another client had marked deleted'

defect 'expunge/uidplus' 'mail_agentd/client.py' \
  '        if not session.has_capability("UIDPLUS"):
            raise Unsupported(
                f"mailbox {self.config.name}: the server lacks UIDPLUS, and a plain EXPUNGE "' \
  '        if False:
            raise Unsupported(
                f"mailbox {self.config.name}: the server lacks UIDPLUS, and a plain EXPUNGE "' \
  'on a server without UIDPLUS the removal fails half way and leaves the message marked deleted'

defect 'session/reconnect' 'mail_agentd/client.py' \
  '                if attempt == 2:' \
  '                if True:' \
  'after a dropped connection every request fails until the service restarts'

defect 'session/auth' 'mail_agentd/client.py' \
  '            raise AuthFailed(f"mailbox {self.config.name}: {refused}") from refused' \
  '            raise' \
  'a wrong password is retried forever as if the network had failed, and the server locks the account'

defect 'folder/special' 'mail_agentd/client.py' \
  '                if flag in flags:' \
  '                if flag in flags or True:' \
  'deleted mail lands in whatever folder the server lists first, not in Trash'

# The handler. What it answers is all the agent learns, so what it leaves out matters
defect 'read/filter' 'mail_agentd/handler.py' \
  '                if not decision.allowed:
                    withheld += 1
                    continue' \
  '                if False:
                    withheld += 1
                    continue' \
  'a listing shows the sender and subject of every message in the folder, granted or not'

defect 'read/one' 'mail_agentd/handler.py' \
  '        raw, decision, identity = await self._message(name, box, request, "read")
        if not decision.allowed:' \
  '        raw, decision, identity = await self._message(name, box, request, "read")
        if False:' \
  'any message is read whole by its UID, whoever wrote it'

defect 'read/own-address' 'mail_agentd/handler.py' \
  '        return [addresses.parse_rule(box.config.address)] + [' \
  '        return [] + [' \
  'the mailbox'\''s own address counts as a second party, and a rule naming it opens every message'

defect 'read/order' 'mail_agentd/handler.py' \
  '        order = uids[:SCAN] if since is not None else list(reversed(uids))[:SCAN]' \
  '        order = uids[:SCAN]' \
  'a listing shows the oldest mail in a folder instead of the newest'

defect 'media/grant' 'mail_agentd/handler.py' \
  '        raw, decision, identity = await self._message(name, box, request, "media")' \
  '        raw, decision, identity = await self._message(name, box, request, "read")' \
  'a grant to read a message also downloads its attachments, which the user granted separately'

defect 'list/grant' 'mail_agentd/handler.py' \
  '        decision = self._store.grant(name).may_mailbox("list")
        if not decision.allowed:' \
  '        decision = self._store.grant(name).may_mailbox("list")
        if False:' \
  'the folder names and counts of a mailbox are shown with no grant on it'

defect 'main/password' 'mail_agentd/__main__.py' \
  '    except FileNotFoundError:
        _stop(f"no {kind} password for mailbox {name} in the credentials directory")' \
  '    except FileNotFoundError:
        return ""' \
  'a mailbox with no password starts anyway and fails every request, instead of the unit stopping once with the reason'

# Sending. What leaves cannot be called back, so every check happens before the send
defect 'send/crlf' 'mail_agentd/compose.py' \
  '    if any(c in value for c in FORBIDDEN):' \
  '    if False:' \
  'a subject with a line break writes a header of its own, a Bcc to anyone among them'

defect 'send/bcc-leak' 'mail_agentd/handler.py' \
  '            cc=lists["cc"],
            subject=request.get("subject", ""),' \
  '            cc=lists["cc"] + lists["bcc"],
            subject=request.get("subject", ""),' \
  'every recipient and the copy in Sent show who was meant to receive the message unseen'

defect 'send/bcc-checked' 'mail_agentd/handler.py' \
  '        decision = self._store.grant(name).may_recipients("send", _everyone(lists))' \
  '        decision = self._store.grant(name).may_recipients("send", lists["to"] + lists["cc"])' \
  'one granted address in To carries any stranger in Bcc, and the user never sees who received it'

defect 'send/envelope' 'mail_agentd/handler.py' \
  '        await self._call(name, box.send, sender, _everyone(lists), raw)' \
  '        await self._call(name, box.send, sender, lists["to"] + lists["cc"], raw)' \
  'a Bcc recipient the user granted never receives the message, and the answer says it was sent'

defect 'send/bare-address' 'mail_agentd/compose.py' \
  '    if len(found) != 1 or addresses.canonical(text) != found[0]:' \
  '    if not found:' \
  'a recipient written as a list sends to every address in it, though only the first was checked'

defect 'send/sent-copy' 'mail_agentd/handler.py' \
  '                await self._call(name, box.append, sent, raw, [client.SEEN])' \
  '                pass' \
  'what the agent sends never appears in Sent, so the user cannot see it from their own client'

defect 'send/after-send' 'mail_agentd/handler.py' \
  '            except (client.NoFolder, *client.LOST) as unfiled:
                warnings.append(f"sent, but no copy was filed in Sent: {unfiled}")' \
  '            except ZeroDivisionError as unfiled:
                warnings.append(f"sent, but no copy was filed in Sent: {unfiled}")' \
  'a missing Sent folder makes a send that already left report failure, and the agent sends it again'

# Answering, forwarding and drafting. The addresses a message will reach are computed here
defect 'reply/reply-to' 'mail_agentd/handler.py' \
  '        targets = addresses.addresses_in(parsed.get_all("Reply-To", []) or parsed.get_all("From", []))' \
  '        targets = addresses.addresses_in(parsed.get_all("From", []))' \
  'a reply to a message whose Reply-To points elsewhere is checked against From and lands with a stranger, or never reaches the address that asked for it'

defect 'reply/read' 'mail_agentd/handler.py' \
  '        raw, read, identity = await self._message(name, box, request, "read")
        if not read.allowed:
            return _refused(read.reason, remedy=read.remedy)
        parsed = message.parse(raw)
        own = self.own_rules(box)' \
  '        raw, read, identity = await self._message(name, box, request, "read")
        parsed = message.parse(raw)
        own = self.own_rules(box)' \
  'the agent answers, and so quotes, a message it was never allowed to read'

defect 'reply/own' 'mail_agentd/handler.py' \
  '        counterparts, _ = message.split_own(list(dict.fromkeys(targets)), own)' \
  '        counterparts, _ = list(dict.fromkeys(targets)), []' \
  'reply-all writes to the mailbox itself and to its aliases, and the reply comes back as new mail'

defect 'reply/alias' 'mail_agentd/handler.py' \
  '        sender = message.own_reached(parsed, own) or box.config.address' \
  '        sender = box.config.address' \
  'a reply to mail sent to an alias comes from the mailbox behind it, and tells the correspondent the address the alias kept private'

defect 'reply/answered' 'mail_agentd/handler.py' \
  '        await self._call(name, box.set_flag, folder, [uid], ANSWERED, uidvalidity=uidvalidity)' \
  '        pass' \
  'the user'\''s client does not show which messages the agent answered'

defect 'reply/mark-read' 'mail_agentd/handler.py' \
  '        if self._store.mailbox_flag(name, "mark-read-on-reply"):' \
  '        if True:' \
  'every reply marks the message read, though the user never asked for it'

defect 'reply/carried' 'mail_agentd/compose.py' \
  '    return checked(" ".join(str(value or "").split()), "a carried header")' \
  '    return str(value or "")' \
  'a folded References header from a stranger reaches the wire with its line breaks, and the reply is refused or malformed'

defect 'forward/source' 'mail_agentd/handler.py' \
  '        raw, read, identity = await self._message(name, box, request, "read")
        if not read.allowed:
            return _refused(read.reason, remedy=read.remedy)
        lists = compose.recipients(request)' \
  '        raw, read, identity = await self._message(name, box, request, "read")
        lists = compose.recipients(request)' \
  'any message is sent on to a granted address, whoever wrote it'

defect 'forward/word' 'mail_agentd/handler.py' \
  '        decision = self._store.grant(name).may_recipients("forward", _everyone(lists))' \
  '        decision = self._store.grant(name).may_recipients("send", _everyone(lists))' \
  'a grant to send also forwards, and mail leaves with its origin to anyone the user lets the agent write to'

defect 'draft/checked' 'mail_agentd/handler.py' \
  '        decision = self._store.grant(name).may_recipients("draft", _everyone(lists))' \
  '        decision = self._store.grant(name).may_recipients("draft", lists["to"])' \
  'a draft carries an ungranted Bcc, and the user sends it from their own client without seeing it'

defect 'draft/bcc' 'mail_agentd/handler.py' \
  '            draft_bcc=lists["bcc"],' \
  '            draft_bcc=(),' \
  'the Bcc the agent was allowed to put in a draft is lost, and the user'\''s client sends it without them'

# Changing messages. All of a request or none of it, each message under its own grant
defect 'change/each' 'mail_agentd/handler.py' \
  '            if not decision.allowed:
                return _refused(f"message {uid}: {decision.reason}", remedy=decision.remedy)' \
  '            if not decision.allowed and uid == uids[0]:
                return _refused(f"message {uid}: {decision.reason}", remedy=decision.remedy)' \
  'a request that names one granted message first deletes every message after it, granted or not'

defect 'change/delete-trash' 'mail_agentd/handler.py' \
  '            await self._call(name, box.trash, folder, uids, uidvalidity=current)' \
  '            await self._call(name, box.expunge, folder, uids, uidvalidity=current)' \
  'a delete destroys the message for good, where the user expected to find it in Trash'

defect 'change/unseen' 'mail_agentd/handler.py' \
  '            await self._call(name, box.set_seen, folder, uids, bool(request.get("seen", True)), uidvalidity=current)' \
  '            await self._call(name, box.set_seen, folder, uids, True, uidvalidity=current)' \
  'a message marked unread by the agent stays read, and the user misses it'

# The client. It shapes requests and remembers what it sent
defect 'cli/named-only' 'mail.py' \
  '    request = {key: value for key, value in named.items() if value is not None}' \
  '    request = dict(named)' \
  'every request carries every field as null, and the service reads an unnamed folder or limit as given'

defect 'cli/remember' 'mail.py' \
  '            SENT.remember(options.mailbox, sent)' \
  '            pass' \
  'what the agent sends to an own address comes back through the watcher as news, and it may answer itself'

defect 'cli/unread' 'mail.py' \
  '        request["seen"] = False' \
  '        request["seen"] = True' \
  'mark-read --unread marks the message read'
