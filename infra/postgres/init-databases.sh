#!/bin/bash
# Chạy một lần khi volume Postgres còn trống.
set -euo pipefail

exists="$(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = 'MLAI-AILUSION'")"
if [ "$exists" != "1" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c 'CREATE DATABASE "MLAI-AILUSION"'
fi
