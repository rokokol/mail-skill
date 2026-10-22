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
