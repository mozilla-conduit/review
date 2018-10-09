#!/usr/bin/env sh
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Test that edits to commits in the middle of a stack are sent to Phabricator.
# See bug 1494372.
#
# Pre-requisites:
#   arc certificate:
#       verified by running: $ echo '{}' | arc call-conduit conduit.ping
#       fixed by running: $ arc install-certificate
#
#   hg evolve extension:
#       verified by running: $ pip list | grep hg-evolve
#       fixed by running: $ pip install --user hg-evolve

set -e

RED="$(tput setaf 1)"
GREEN="$(tput setaf 2)"
BOLD="$(tput bold)"
RESET="$(tput sgr0)"

if [ ! -f clone_repositories.sh ]
then
    echo "Error: could not locate the clone_repositories.sh script."
    echo
    echo "Run this script from the same directory that contains the"
    echo "clone_repositories.sh script."
fi

./clone_repositories.sh
cd test-repo

cat <<EOF >>.hg/hgrc
[extensions]
evolve=
EOF
echo "${BOLD}${RED}Evolve extension activated!${RESET}"

echo a > X
hg add X
hg commit -m A
echo b > X
hg commit -m B
echo c > X
hg commit -m C
hg log -G
hg status
moz-phab submit -y -b 1

echo
echo "${BOLD}${GREEN}Initial commit stack created${RESET}"
echo "${BOLD}${GREEN}Editing commit mid-stack${RESET}"
echo

# Check out 'b'
hg checkout tip
hg checkout .^

hg log -r . -T '{desc}\n' > /tmp/msg
sed -i -e '1 cBug 1 - new title B!' /tmp/msg

hg commit --amend --logfile /tmp/msg
hg evolve --all
hg log -G
moz-phab submit -y

echo
echo "${BOLD}${GREEN}Revision title and description should be updated${RESET}"
