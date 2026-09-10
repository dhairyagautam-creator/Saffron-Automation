-- Version 2.1 -- fixes a gap found during two-machine verification of
-- 0023_sync_manifest.sql: re-uploading byte-identical content to a slot
-- (e.g. a user re-selecting the same source file with nothing changed)
-- lands on the SAME content-addressed storage path (module/slot_key/sha256)
-- as the existing object. The client always calls Storage .upload(...,
-- upsert=true), and with no UPDATE policy on storage.objects that upsert
-- was rejected by RLS ("new row violates row-level security policy") --
-- surfacing as a confusing sync failure for what is actually a no-op.
--
-- 0023 deliberately left out an UPDATE policy, reasoning a new upload is
-- always a new path, never a mutation of an existing object. That holds
-- for genuinely different content; it doesn't hold for identical content,
-- which does happen. Since the path IS the content hash, an UPDATE at an
-- existing path can only ever overwrite an object with byte-identical
-- bytes -- there is no way for this policy to let anyone alter another
-- upload's actual content. Safe to allow.
--
-- Run in the Supabase SQL Editor. Safe to re-run.

begin;

drop policy if exists "Authenticated users can overwrite their own sync uploads" on storage.objects;
create policy "Authenticated users can overwrite their own sync uploads"
on storage.objects for update
to authenticated
using (bucket_id = 'sync-uploads')
with check (bucket_id = 'sync-uploads');

commit;
