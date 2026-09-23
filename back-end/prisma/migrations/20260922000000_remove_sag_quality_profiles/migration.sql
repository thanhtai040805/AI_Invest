-- Remove the retired SAG/MOAT quality-profile contract from core storage.
-- Do not use CASCADE: unexpected dependents must fail the migration visibly.
-- Existing rows need an explicit archive decision before this migration runs.

DO $$
DECLARE
    table_name text;
    has_rows boolean;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['business_quality_profiles', 'moat_profiles'] LOOP
        IF to_regclass(format('public.%I', table_name)) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I)', table_name) INTO has_rows;
            IF has_rows THEN
                RAISE EXCEPTION 'Refusing to drop nonempty table %. Back up and archive it first.', table_name;
            END IF;
        END IF;
    END LOOP;
END $$;

DROP TABLE IF EXISTS "business_quality_profiles";
DROP TABLE IF EXISTS "moat_profiles";
