#!/bin/sh
# Re-capture the shipped bd CLI's help for the two write verbs (bead mem-e4fby, A1.4).
#
# The supported-flag set pinned in verbs.py is derived MECHANICALLY from these
# captured files by cligrammar.help_flag_names(). tests/test_e0_rates.py re-derives
# it from the committed text and fails on drift. Nothing in the analysis path runs
# the binary, so a published number never depends on which bd is on PATH.
set -eu
cd "$(dirname "$0")"
bd --version       > bd-version.txt     2>&1
bd remember --help > remember.help.txt  2>&1
bd forget --help   > forget.help.txt    2>&1
sha256sum bd-version.txt remember.help.txt forget.help.txt > shipped-cli-help.sha256
