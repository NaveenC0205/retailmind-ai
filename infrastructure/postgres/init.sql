-- pgvector + the three logical schemas. In the local (SQLite) profile the same
-- separation is expressed as a table-name prefix.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS retail;
CREATE SCHEMA IF NOT EXISTS ai;
CREATE SCHEMA IF NOT EXISTS eval;
