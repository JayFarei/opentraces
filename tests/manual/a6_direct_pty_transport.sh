#!/bin/sh
set -eu

workspace=$1
product_harness=$2
printf 'A6_DIRECT_PTY_PWD=%s\n' "$PWD"
sleep 2
exec /usr/bin/sudo -H -n -u opentraces-product -- \
  /bin/sh "$product_harness" "$workspace"
