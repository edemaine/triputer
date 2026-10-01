#!/bin/sh
# Shared by pi and upload. .env uses shell assignment syntax.
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -f "$PROJECT_ROOT/.env" ]; then
    . "$PROJECT_ROOT/.env"
fi
: "${PI:?Set PI=user@hostname in .env (see .env.example), or export PI}"
