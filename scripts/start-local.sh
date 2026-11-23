#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  password=$(openssl rand -hex 24)
  umask 077
  (
    set -C
    while IFS= read -r line; do
      case "$line" in
        POSTGRES_PASSWORD=) printf 'POSTGRES_PASSWORD=%s\n' "$password" ;;
        *) printf '%s\n' "$line" ;;
      esac
    done < .env.example > .env
  )
fi

docker compose up -d --build --wait
printf '\nSwagger UI RiskFlow: http://127.0.0.1:8000/docs\n'
