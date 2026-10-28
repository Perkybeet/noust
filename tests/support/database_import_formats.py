# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_import_formats.py`` that other tests build on."""

from __future__ import annotations

PG_CUSTOM = b"PGDMP\x01\x0e\x00" + b"\x00" * 64

DUMP_WITH_OWNERS = """\
--
-- PostgreSQL database dump
--

SET statement_timeout = 0;
CREATE TABLE public.users (id integer, note text);
ALTER TABLE public.users OWNER TO app_prod;
CREATE FUNCTION public.f() RETURNS text
    LANGUAGE plpgsql AS $$
BEGIN
GRANT ALL ON TABLE public.users TO nobody;
ALTER TABLE public.users OWNER TO inside_a_function;
RETURN 'x';
END;
$$;
ALTER FUNCTION public.f() OWNER TO app_prod;
COPY public.users (id, note) FROM stdin;
1	ALTER TABLE public.users OWNER TO data_not_a_statement;
2	GRANT ALL ON TABLE public.users TO data_not_a_grant;
\\.
GRANT SELECT ON TABLE public.users TO reader;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE app_prod IN SCHEMA public GRANT SELECT ON TABLES TO reader;
INSERT INTO public.users VALUES (3, 'GRANT ALL ON TABLE t TO x;');
"""
