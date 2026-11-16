#!/bin/bash
# Open this file in Terminal by double-clicking in Finder.
script_dir="$(cd "$(dirname "$0")" && pwd)" || exit 1
exec /usr/bin/caffeinate -i /bin/bash "$script_dir/scripts/run_local.sh"
