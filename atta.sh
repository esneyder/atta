#!/usr/bin/env sh
# Atta launcher for macOS and Linux: ./atta.sh <command>
exec python3 "$(dirname "$0")/atta/bin/atta.py" "$@"
