BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
SET LOCAL idle_in_transaction_session_timeout = '10s';
SET LOCAL work_mem = '4MB';
SET LOCAL temp_file_limit = '64MB';
SET LOCAL max_parallel_workers_per_gather = 0;

WITH users AS (
    SELECT
        count(*) FILTER (WHERE disabled_at IS NULL) AS enabled,
        count(*) FILTER (WHERE disabled_at IS NOT NULL) AS disabled,
        count(*) FILTER (WHERE created_at >= now() - interval '24 hours' AND created_at <= now()) AS day,
        count(*) FILTER (WHERE created_at >= now() - interval '7 days' AND created_at <= now()) AS week,
        count(*) FILTER (WHERE created_at >= now() - interval '30 days' AND created_at <= now()) AS month
    FROM public.users
), books AS (
    SELECT
        count(*) FILTER (WHERE NOT is_physical) AS digital,
        count(*) FILTER (WHERE is_physical) AS physical
    FROM public.books
), assets AS MATERIALIZED (
    SELECT
        CASE WHEN kind IN ('book_file', 'cover_image') THEN kind ELSE 'other' END AS kind,
        CASE
            WHEN kind = 'book_file' AND lower(extension) IN ('epub', 'pdf', 'mobi', 'azw3', 'txt', 'cbr', 'cbz') THEN lower(extension)
            WHEN kind = 'cover_image' AND lower(extension) IN ('jpg', 'jpeg', 'png', 'webp', 'gif') THEN lower(extension)
            ELSE 'other'
        END AS format,
        size_bytes,
        lower(sha256) AS checksum
    FROM public.media_assets
), files AS (
    SELECT kind, format, count(*) AS files, sum(size_bytes) AS bytes
    FROM assets
    GROUP BY kind, format
)
SELECT jsonb_build_object(
    'users', jsonb_build_object('enabled', users.enabled, 'disabled', users.disabled),
    'registrations', jsonb_build_object('24h', users.day, '7d', users.week, '30d', users.month),
    'books', jsonb_build_object('digital', books.digital, 'physical', books.physical),
    'distinct_uploaded_contents', (SELECT count(DISTINCT checksum) FROM assets WHERE kind = 'book_file' AND checksum ~ '^[0-9a-f]{64}$'),
    'files', COALESCE((SELECT jsonb_agg(to_jsonb(bucket)) FROM files AS bucket), '[]'::jsonb)
)
FROM users CROSS JOIN books;
COMMIT;
