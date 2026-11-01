#!/bin/sh
set -e

clickhouse client -n <<-EOSQL
  CREATE DATABASE IF NOT EXISTS openpanel;
EOSQL
