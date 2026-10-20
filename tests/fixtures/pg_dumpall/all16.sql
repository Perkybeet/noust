--
-- PostgreSQL database cluster dump
--

\restrict FoyzoPafxN6PZqXpl97jRohKBy353MNfXw9QQoMdPNq4vrxUDADOle0fceI8CFf

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Roles
--

CREATE ROLE bossman;
ALTER ROLE bossman WITH SUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:xXPAgeXkhXbBqGrK8diWKQ==$aIC8dK4ZBVHEKs/aEqeXZkTWiVuJlw/aDQSV0bFTA9o=:HHOfa6xtW+8nBfFwULZTWeZ7uyMfWd0Keyd6xRtDmNo=';
CREATE ROLE legacy;
ALTER ROLE legacy WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD 'SCRAM-SHA-256$4096:Te7bT4I+Onzr1pk9T8fm1g==$1Cgu54J+549iz/qIfCYp1i41yHU2tRYFM7pUevCFuhg=:AVDOMYIyymlkuFUbdLL8KtrHGtJ1PASCJ5POpCm+/uw=' VALID UNTIL '2030-01-01 00:00:00+00';
CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:yFCjgclbrPq74rPN1OPaCg==$JNlMum3/2g+JAnQHW7eTxQCkeJ+diCqm3mDiv5FQuFA=:qA3Gtq1Am0LCOI2f9zMyYm/8csbj1tBSzCTm8s6Q4VQ=';
CREATE ROLE reporting;
ALTER ROLE reporting WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE shopapp;
ALTER ROLE shopapp WITH NOSUPERUSER INHERIT NOCREATEROLE CREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:WH6OIIaazlkGoa5Fz5ehow==$w/Fv72JS7ZC7rWVQAp5cTcUybEi0XvD5qMkQ1q7G/kI=:/4HuKmYsbkqw/SpdWehE3elXJQoS3X58rlDXnLuclPg=';

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






\unrestrict FoyzoPafxN6PZqXpl97jRohKBy353MNfXw9QQoMdPNq4vrxUDADOle0fceI8CFf

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

\restrict SS1d6zPiWBhSCnEO895tKUQ0Sc2AQ4d37KNw5tsbRpprcCXGiIeN66gnO2w6H0b

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
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

\unrestrict SS1d6zPiWBhSCnEO895tKUQ0Sc2AQ4d37KNw5tsbRpprcCXGiIeN66gnO2w6H0b

--
-- Database "blog" dump
--

--
-- PostgreSQL database dump
--

\restrict dEtA1TWHKhQ9R1dQsROCUQr4EcT3IrtSbrHuMSpyealI1nefG1brVkVu2ViKphx

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
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

\unrestrict dEtA1TWHKhQ9R1dQsROCUQr4EcT3IrtSbrHuMSpyealI1nefG1brVkVu2ViKphx
\connect blog
\restrict dEtA1TWHKhQ9R1dQsROCUQr4EcT3IrtSbrHuMSpyealI1nefG1brVkVu2ViKphx

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
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

\unrestrict dEtA1TWHKhQ9R1dQsROCUQr4EcT3IrtSbrHuMSpyealI1nefG1brVkVu2ViKphx

--
-- Database "postgres" dump
--

\connect postgres

--
-- PostgreSQL database dump
--

\restrict TrVt5O3meDfYExor0zDwmdkBNJmhsY3wh3x44QUXpKLIJCgiSMdgCn869WowcDy

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
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

\unrestrict TrVt5O3meDfYExor0zDwmdkBNJmhsY3wh3x44QUXpKLIJCgiSMdgCn869WowcDy

--
-- Database "shop" dump
--

--
-- PostgreSQL database dump
--

\restrict CwLdT3lRelSTYJ1NhqmolKB9j3PO6qdD2LAmmSfKJG0RfKeX7OQGdNEJaxprDhf

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
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

\unrestrict CwLdT3lRelSTYJ1NhqmolKB9j3PO6qdD2LAmmSfKJG0RfKeX7OQGdNEJaxprDhf
\connect shop
\restrict CwLdT3lRelSTYJ1NhqmolKB9j3PO6qdD2LAmmSfKJG0RfKeX7OQGdNEJaxprDhf

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
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

\unrestrict CwLdT3lRelSTYJ1NhqmolKB9j3PO6qdD2LAmmSfKJG0RfKeX7OQGdNEJaxprDhf

--
-- PostgreSQL database cluster dump complete
--

