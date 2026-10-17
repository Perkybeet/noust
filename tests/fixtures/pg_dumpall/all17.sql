--
-- PostgreSQL database cluster dump
--

\restrict I1hRGqXZcvosxUw4vovEZtRTJxVsSRqbd0JKMYX7omNPcGqs8atrfcVijEHF39U

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Roles
--

CREATE ROLE bossman;
ALTER ROLE bossman WITH SUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:yrQlgpcQbcFmQ19O/A1P1w==$GcDNr8ZU1v4UmK9RV0wviehWrPoo3uX5yZIOpA1rN60=:rHYukiCyhkIZs1ocUbdoQLMHzPdOBNpNlhk86MPrNHk=';
CREATE ROLE legacy;
ALTER ROLE legacy WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD 'SCRAM-SHA-256$4096:dGByqjuzwDog3OWFX8Mjrw==$8SLqAgglBMqM7zD2ttSXihQLx/d+atJ7M56it4O98QA=:jtP/bPPpd+K1UwO+T42JykYIvZANAln4nBYo+s7o6ZQ=' VALID UNTIL '2030-01-01 00:00:00+00';
CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:TrIPESVT81ZLnUeN6rFuMQ==$kA3V65eCh/EMi5BQOyC7N/MnCuxL6TzVUElkeCTEQQU=:vYERPxM+i7C7w3Djo1bBhznJHZatAHcp8SYJGxoWZk0=';
CREATE ROLE reporting;
ALTER ROLE reporting WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE shopapp;
ALTER ROLE shopapp WITH NOSUPERUSER INHERIT NOCREATEROLE CREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:cDtv/sKoffc/WfeAWrhdkQ==$xXQM8CfvdbYIRuPruN9C9l5Q8Zsgx5VppEk1qZOaC/Q=:IYYnBkoypyDGVl7sWCpIUkcjUMQMHcXQh22Lyjdz0/0=';

--
-- User Configurations
--

--
-- User Config "shopapp"
--

ALTER ROLE shopapp SET search_path TO 'shop', 'public';


--
-- Role memberships
--

GRANT reporting TO shopapp WITH INHERIT TRUE GRANTED BY postgres;






\unrestrict I1hRGqXZcvosxUw4vovEZtRTJxVsSRqbd0JKMYX7omNPcGqs8atrfcVijEHF39U

--
-- Databases
--

--
-- Database "template1" dump
--

\connect template1

--
-- PostgreSQL database dump
--

\restrict r5aUeF6G4Yl1kNLfsr4ROwfhFCmtG1c2aDu9CGxu3idGSaAR1ZaDde0AhJsXiWl

-- Dumped from database version 17.11 (Debian 17.11-1.pgdg13+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- PostgreSQL database dump complete
--

\unrestrict r5aUeF6G4Yl1kNLfsr4ROwfhFCmtG1c2aDu9CGxu3idGSaAR1ZaDde0AhJsXiWl

--
-- Database "blog" dump
--

--
-- PostgreSQL database dump
--

\restrict pgsEZVaiEucyqp2ZWtiyCtepsVaTKhcnKzUSvqM1axRSVjSoEccO0HPFxBSUIIJ

-- Dumped from database version 17.11 (Debian 17.11-1.pgdg13+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: blog; Type: DATABASE; Schema: -; Owner: legacy
--

CREATE DATABASE blog WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE blog OWNER TO legacy;

\unrestrict pgsEZVaiEucyqp2ZWtiyCtepsVaTKhcnKzUSvqM1axRSVjSoEccO0HPFxBSUIIJ
\connect blog
\restrict pgsEZVaiEucyqp2ZWtiyCtepsVaTKhcnKzUSvqM1axRSVjSoEccO0HPFxBSUIIJ

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: posts; Type: TABLE; Schema: public; Owner: legacy
--

CREATE TABLE public.posts (
    id integer,
    body text
);


ALTER TABLE public.posts OWNER TO legacy;

--
-- Data for Name: posts; Type: TABLE DATA; Schema: public; Owner: legacy
--

COPY public.posts (id, body) FROM stdin;
1	-- Database "shop" dump
2	x\n\\connect shop
\.


--
-- Name: DATABASE blog; Type: ACL; Schema: -; Owner: legacy
--

REVOKE CONNECT,TEMPORARY ON DATABASE blog FROM PUBLIC;
GRANT TEMPORARY ON DATABASE blog TO PUBLIC;


--
-- PostgreSQL database dump complete
--

\unrestrict pgsEZVaiEucyqp2ZWtiyCtepsVaTKhcnKzUSvqM1axRSVjSoEccO0HPFxBSUIIJ

--
-- Database "postgres" dump
--

\connect postgres

--
-- PostgreSQL database dump
--

\restrict b27fqY7Yhy5o0CBnCQuQ4GUUk1d9B5haJJvV7d3o144LK9FE9zBxRv7ZximhJc4

-- Dumped from database version 17.11 (Debian 17.11-1.pgdg13+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- PostgreSQL database dump complete
--

\unrestrict b27fqY7Yhy5o0CBnCQuQ4GUUk1d9B5haJJvV7d3o144LK9FE9zBxRv7ZximhJc4

--
-- Database "shop" dump
--

--
-- PostgreSQL database dump
--

\restrict o8q2rh5HVxuZU2XV5Of9HmoYT2bTYAfRQzxErtnvN07tM7pAIlyFIJDeBath95h

-- Dumped from database version 17.11 (Debian 17.11-1.pgdg13+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: shop; Type: DATABASE; Schema: -; Owner: shopapp
--

CREATE DATABASE shop WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE shop OWNER TO shopapp;

\unrestrict o8q2rh5HVxuZU2XV5Of9HmoYT2bTYAfRQzxErtnvN07tM7pAIlyFIJDeBath95h
\connect shop
\restrict o8q2rh5HVxuZU2XV5Of9HmoYT2bTYAfRQzxErtnvN07tM7pAIlyFIJDeBath95h

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: f(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.f() RETURNS integer
    LANGUAGE plpgsql
    AS $$ begin return 1; end $$;


ALTER FUNCTION public.f() OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: orders; Type: TABLE; Schema: public; Owner: shopapp
--

CREATE TABLE public.orders (
    id integer NOT NULL,
    note text
);


ALTER TABLE public.orders OWNER TO shopapp;

--
-- Name: orders_id_seq; Type: SEQUENCE; Schema: public; Owner: shopapp
--

CREATE SEQUENCE public.orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.orders_id_seq OWNER TO shopapp;

--
-- Name: orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: shopapp
--

ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id;


--
-- Name: orders id; Type: DEFAULT; Schema: public; Owner: shopapp
--

ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass);


--
-- Data for Name: orders; Type: TABLE DATA; Schema: public; Owner: shopapp
--

COPY public.orders (id, note) FROM stdin;
1	o1
2	o2
3	o3
4	line one\\;\nline two -- not a comment
\.


--
-- Name: orders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: shopapp
--

SELECT pg_catalog.setval('public.orders_id_seq', 4, true);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: shopapp
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: DATABASE shop; Type: ACL; Schema: -; Owner: shopapp
--

GRANT CONNECT ON DATABASE shop TO reporting;


--
-- Name: TABLE orders; Type: ACL; Schema: public; Owner: shopapp
--

GRANT SELECT ON TABLE public.orders TO reporting;


--
-- PostgreSQL database dump complete
--

\unrestrict o8q2rh5HVxuZU2XV5Of9HmoYT2bTYAfRQzxErtnvN07tM7pAIlyFIJDeBath95h

--
-- PostgreSQL database cluster dump complete
--

