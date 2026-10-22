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
