CREATE ROLE experdba WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'experdba';
CREATE DATABASE experdb WITH OWNER experdba;
CREATE ROLE repluser WITH LOGIN REPLICATION PASSWORD 'repluser';
