#!/bin/sh
set -eu

IFS= read -r _prompt
printf 'A6_DIRECT_PTY_PWD=%s\n' "$PWD"
sleep 2
test "$PWD" = "$1"
test "$(id -un)" = opentraces-product
test "$(id -u)" -ne 0
! sudo -n true >/dev/null 2>&1
test "$(stat -c %U .)" = opentraces-product
mkdir -p .opentraces/bench-capture/direct-pty-control
printf '%s\n' '{"completeness":"complete"}' \
  > .opentraces/bench-capture/direct-pty-control/capture_result.json
printf '%s\n' 'agent edit' > a6-direct-pty-proof.txt
git config user.name 'A6 product identity'
git config user.email 'a6-product@example.invalid'
git add a6-direct-pty-proof.txt
git commit -m 'Prove direct PTY workspace' >/dev/null
test "$(git show HEAD:a6-direct-pty-proof.txt)" = 'agent edit'
printf '%s\n' A6_DIRECT_PTY_WORKSPACE_OK
sleep 2
