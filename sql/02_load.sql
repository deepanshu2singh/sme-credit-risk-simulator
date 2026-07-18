-- =============================================================================
--  SME Open Banking Credit Risk Simulator
--  02_load.sql  —  bulk-load the CSVs into the schema
--
--  Run from the directory that contains the CSV files, e.g.:
--      cd output/
--      psql "postgresql://user:pass@host:5432/dbname" -f ../sql/02_load.sql
--
--  Uses \copy (client-side) so it works over any connection without needing
--  server-side file access or superuser rights (works on Supabase, RDS, etc.).
--  Load order respects foreign keys: parent (sme_profiles) first.
--
--  CSV NULL handling: empty fields are treated as SQL NULL, which correctly
--  maps empty term_months -> NULL and empty subcategory -> NULL.
-- =============================================================================

-- 1. Dimension first (parent of all FKs)
\copy sme_profiles      FROM 'sme_profiles.csv'      WITH (FORMAT csv, HEADER true, NULL '');

-- 2. Reference / facility table
\copy credit_facilities FROM 'credit_facilities.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- 3. High-volume fact table
\copy daily_transactions FROM 'daily_transactions.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- 4. Balance snapshot aggregate
\copy daily_balances    FROM 'daily_balances.csv'    WITH (FORMAT csv, HEADER true, NULL '');

-- 5. Macro factor
\copy macro_index       FROM 'macro_index.csv'       WITH (FORMAT csv, HEADER true, NULL '');

-- Refresh planner statistics after bulk load
ANALYZE sme_profiles;
ANALYZE credit_facilities;
ANALYZE daily_transactions;
ANALYZE daily_balances;
ANALYZE macro_index;
