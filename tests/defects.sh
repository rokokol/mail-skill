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
