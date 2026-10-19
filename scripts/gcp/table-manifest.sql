-- Invoked with psql -v relation=public.table -v schema_only=true|false.
-- Keep deparsing, JSON dates, binary and floating point output stable across hosts.
SET timezone = 'UTC';
SET datestyle = 'ISO, YMD';
SET intervalstyle = 'postgres';
SET extra_float_digits = 3;
SET search_path = pg_catalog;
SELECT format($sql$
SELECT jsonb_build_object(
  'table', %1$L,
  'rows', count(*),
  'content_md5', md5(coalesce(string_agg(row_hash, '' ORDER BY row_hash COLLATE "C"), '')),
  'columns', (SELECT jsonb_agg(jsonb_build_array(a.attname, format_type(a.atttypid,a.atttypmod),
      a.attnotnull, a.attidentity, a.attgenerated, pg_get_expr(d.adbin,d.adrelid),
      a.attcollation::regcollation::text, a.attcompression) ORDER BY a.attnum)
    FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attrelid=%1$L::regclass AND a.attnum>0 AND NOT a.attisdropped),
  'constraints', (SELECT coalesce(jsonb_agg(jsonb_build_array(c.conname,pg_get_constraintdef(c.oid),
      c.convalidated) ORDER BY c.conname COLLATE "C"),'[]'::jsonb)
    FROM pg_constraint c WHERE c.conrelid=%1$L::regclass),
  'indexes', (SELECT coalesce(jsonb_agg(jsonb_build_array(i.indexrelid::regclass::text,
      pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready) ORDER BY i.indexrelid::regclass::text COLLATE "C"),'[]'::jsonb)
    FROM pg_index i WHERE i.indrelid=%1$L::regclass),
  'triggers', (SELECT coalesce(jsonb_agg(jsonb_build_array(t.tgname,pg_get_triggerdef(t.oid),t.tgenabled)
      ORDER BY t.tgname COLLATE "C"),'[]'::jsonb) FROM pg_trigger t
    WHERE t.tgrelid=%1$L::regclass AND NOT t.tgisinternal),
  'schema_objects', CASE WHEN %4$L::boolean THEN jsonb_build_object(
    'extensions', (SELECT jsonb_agg(jsonb_build_array(e.extname,e.extversion,n.nspname) ORDER BY e.extname COLLATE "C")
      FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace),
    'functions', (SELECT coalesce(jsonb_agg(pg_get_functiondef(p.oid) ORDER BY p.oid::regprocedure::text COLLATE "C"),'[]'::jsonb)
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND NOT EXISTS (SELECT FROM pg_depend d
        WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')),
    'enums', (SELECT coalesce(jsonb_agg(jsonb_build_array(t.typname,
      (SELECT jsonb_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_enum e WHERE e.enumtypid=t.oid))
      ORDER BY t.typname COLLATE "C"),'[]'::jsonb)
      FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' AND t.typtype='e')) ELSE NULL END,
  'sequences', (SELECT coalesce(jsonb_agg(jsonb_build_array(s.seqrelid::regclass::text,
      a.attname,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle)
      ORDER BY s.seqrelid::regclass::text COLLATE "C"),'[]'::jsonb)
    FROM pg_sequence s JOIN pg_depend d ON d.objid=s.seqrelid
      JOIN pg_attribute a ON a.attrelid=d.refobjid AND a.attnum=d.refobjsubid AND NOT a.attisdropped
    WHERE d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
      AND d.refobjid=%1$L::regclass AND d.deptype IN ('a','i')))
FROM (SELECT md5(row_to_json(t)::text) AS row_hash FROM %2$s t %3$s) hashed;
$sql$, :'relation', :'relation'::regclass,
  CASE WHEN :'schema_only'::boolean THEN 'WHERE false' ELSE '' END, :'full_schema')
\gexec
