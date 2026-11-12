-- I16 step 7: facts of the database for the transfer and its check. Run by psql inside the PC container
-- (tools/export_dwh_for_server.ps1) and on the server (dwh_import_db.py). Reads only.
\pset tuples_only on
\pset format unaligned
\o /tmp/dwhexp/info.txt
SELECT 'owner=' || pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()
UNION ALL SELECT 'encoding=' || pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()
UNION ALL SELECT 'collate=' || datcollate FROM pg_database WHERE datname = current_database()
UNION ALL SELECT 'ctype=' || datctype FROM pg_database WHERE datname = current_database()
UNION ALL SELECT 'server_version_num=' || current_setting('server_version_num');
\o /tmp/dwhexp/counts.txt
\i /tmp/dwhexp/table_counts.sql
\o
