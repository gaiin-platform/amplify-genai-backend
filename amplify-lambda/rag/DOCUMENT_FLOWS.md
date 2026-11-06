# RAG documents: flows, migration effects, orphans

How each kind of document moves through the system, what the legacy-to-global2 migration changes for it, and how the
orphan cleanup decides what is safe to remove. Code: `rag/core.py` (ingest), `rag/migration*.py` (migration),
`rag/orphans.py` and `rag/retention.py` (cleanup), `rag/migration_costs.py` (cost estimate and spend),
`embedding/embedding-dual-retrieval.py` and `embedding/migration_hook.py` (retrieval and aliasing),
`amplify-lambda-js/datasource/datasources.js` (chat side; also stamps when a document was last used).

"Verified" means it was read in dev data or code; "to check" means it was read in code but not exercised, so look at it on
the first small batch. Counts are from the dev environment on 2026-11-04.

## 1. The pieces every document is made of

```
 user-files table           hash-files table                    object-access table
 id = user file key         id = user file key  (upload pointer)   object_id = CONTENT key
 name, type, tags           id = sha256          (content row)       principal_id = user | astp/<assistant> | group
 data{astp,sourceUrl,...}   textLocationKey ---> CONTENT key
        |                          ^
        |  dochash                 |  TextLocationIndex finds every upload of one content
        v                          |
 S3 rag-input  <-- raw file (key = user file key)

 CONTENT key =  global/<sha256>.content.json   (old: vectors in Postgres)
                global2/<sha256>.content.json  (new: vectors in DynamoDB, Nova)

 S3 file-text  <-- extracted text (key = CONTENT key), + for global2: .manifest.json and page images
 embedding-progress table  object_id = CONTENT key (status, chunks)
 vectors:  Postgres rows (src = global/..)   or   DynamoDB rows (source_id = global2/..)
```

Identical bytes share one CONTENT key. Many uploads can point at it. That is why deleting or migrating is always about
the CONTENT key, and why a delete must never remove shared content while another upload or an assistant still uses it.

## 2. One flow per document type

### A. Chat attachment with RAG off (document cache)
```
 upload -> S3 rag-input (rag_enabled=false) -> process_document
        -> text in file-text (global2/..), upload pointer, access row for the user
        -> NO chunking queue, NO embeddings
 chat   -> JS: user key -> hash row -> CONTENT key -> read text from S3 -> split to fit the model
 migration: not touched (nothing embedded). Verified: it has no progress row.
 later attached to an assistant from the library: it is embedded at that point (see I).
```

### B. File attached to a chat with RAG on, or kept in the library with embeddings
```
 upload -> process_document -> text + page manifest/images -> chunk queue -> embed -> vectors + progress
 chat   -> JS: user key -> hash row -> CONTENT key
        -> dual retrieval: access gate (the user) -> vectors for CONTENT key
 migration: rebuilt as global2 from the ORIGINAL file; the user's pointer is repointed at switch.
```

### C. File uploaded in an assistant's editor
```
 save   -> assistant record dataSources[].id = global/<hash>  (or global2/<hash> for new uploads)   [verified in dev: 244 of these]
 chat   -> JS sends it as an assistant source -> dual retrieval:
           access gate: the assistant principal (astp/..) on that CONTENT key
           -> vectors
 migration: the assistant record is NOT edited. After a switch, retrieval swaps global/X for global2/X
            (alias, after the access gates) and reports results under the id the assistant stores.
```

### D. Scraped web page on an assistant
```
 scrape  -> one file per page: user-files row {tags website, data.isScrapedContent, data.sourceUrl, data.astp}
            -> process_document (RAG on) -> CONTENT key; access row for astp/..
 save    -> assistant dataSources[].id = s3://<user file key>   (NOT global/..)       [verified in dev: 347 of these]
            assistant data.websiteUrls = the site list and scan times
 chat    -> JS: s3://<user key> -> hash row -> s3://<CONTENT key> -> dual retrieval as in C
 migration: follows the hash row. At switch the pointer moves to global2/..; access was copied first, so
            the assistant's reads follow with no edit to the assistant.
 rescan  -> new page files are created; the editor deletes the ones the assistant dropped. Pages left behind
            are the "website orphans".
```

### E. Cloud-drive file (SharePoint and the like) on an assistant
```
 sync    -> user-files row {tags drive-integration, data.originalFileId, data.astp}; markdown/PDF -> CONTENT key
 save    -> assistant dataSources[].id = s3://<user file key>; data.integrationDriveData lists folders/files synced
 chat/migration: same as D (follows the hash row).
 a file removed from the drive/assistant but still in user-files is a "drive orphan".
```

### F. Group assistant
```
 group files live under the GROUP id in the key (<groupId>/date/uuid) and in the group's own space
 save   -> assistant dataSources[].id = s3://<group file key>  [verified: 73]   or global/<hash> [verified: 34]
 chat   -> group assistant path: access is the group/system user, not the chatting user (to check on first batch)
 migration: same two cases as C and D.
 a group file no assistant mentions is a "group orphan".
```

### G. Layered assistants
```
 a layered assistant stores a tree of leaf assistants; the files belong to the leaves (C-F).
 migration needs nothing special: leaves are resolved first, then the same retrieval path.
```

### H. Images and video
```
 stored in the image bucket by key, never embedded, never migrated, not covered by orphan cleanup.
```

### I. A RAG-off library file attached to an assistant later
```
 save   -> the assistant editor sends EVERY library file it just attached to /embedding/check-completion
           (global2/ and global/ alike; it never blocks the save)
 server -> ids_to_embed (embedding-dual-retrieval.py):
             migrated legacy id (cutover/cleaned) ........ swapped to its global2 id, then checked as below
             global2/X ................................... no progress row: run process_document with rag_enabled
                                                           forced on for that run (stored text reused, page manifest
                                                           and images built, then chunking and embedding)
             global/X WITH a progress row ................ left alone: a legacy document is never re-embedded
                                                           (that would write new Postgres rows)
             global/X with NO progress row (never embedded, e.g. a chat-only upload from before the new pipeline)
                                                        .. built as its global2 twin from the original upload
 chat   -> retrieval (alias_never_embedded, after the access gates): a global/X with no embeddings of its own whose
           global2 twin has a progress row is answered from the twin and reported under the id the assistant stores.
           Until the twin has a progress row (seconds to a minute after the save) it is still searched in Postgres and
           returns nothing, as before.
```

## 3. What each migration step changes, per type

| Step | A chat RAG-off | B library RAG-on | C assistant `global/X` | D/E/F `s3://user key` |
|---|---|---|---|---|
| Rebuild (build global2 twin) | nothing | twin built, live path untouched | twin built | twin built |
| Switch | nothing | pointer -> global2, access copied first | alias turns on (the migration record says switched) | pointer -> global2, access copied first |
| Roll back | nothing | pointer -> global/ | alias off within 60 s | pointer -> global/ |
| Remove old copies | nothing | Postgres rows deleted; S3 text, progress row and access rows kept | same | same |

Assistants that stored `global/<hash>` (type C) are changed too, by Move references (`rag/assistant_refs.py`, endpoint
`/assistant/migrate_references` in the assistants service, admin only, called with the admin's own token):

```
 Move references (docs D1..Dn)                 Roll back
   |                                              |
   |- copy access, repoint file records           |- repoint file records back
   `- for each assistant whose NEWEST version     `- for each assistant whose newest version
        stores global/Di:                              stores global2/Di:
        dataSources[].id  global/Di -> global2/Di      dataSources[].id  global2/Di -> global/Di
        (in place, only if the record is unchanged     (same rule)
         since it was read; no new version, alias
         or permission; older versions untouched)
 A failure on one assistant never undoes the move; pressing Move references again repeats it safely, and it also
 catches up documents that were already moved but whose assistants still hold the old id (the Move references
 count includes them).
 An automatic switch (the 1-minute check, "switch automatically") has no admin login, so it cannot edit assistants:
 the next press of Move references does that part.
```

The retrieval swap stays as the safety net for everything that still holds an old id (older versions, API clients, saved chats).

Why users do not see anything break:

1. Build is side by side. Nothing live changes until Switch; the new copy is a separate key.
2. Access is copied before the pointer moves (`copy_access`), and never overwritten.
3. Retrieval aliasing happens AFTER the access gates, so permission checks keep using the id the caller sent, and
   results are mapped back to that id (source panels and citations keep working).
4. The chat service caches "user key -> content key" in memory with no expiry (`lru(500, 0)` in `datasources.js`).
   A warm container can keep sending the old `global/X` after a switch. This is harmless: retrieval aliases
   `global/X` to `global2/X` for switched documents, and the legacy S3 text is never deleted by cleanup. After a
   rollback, a cached `global2/X` also still works because the twin is not deleted.
5. Remove old copies is guarded: the new copy must be complete and have vectors, no upload may still point at the old
   key, access is copied once more first, and the Postgres delete accepts only `global/` keys. It runs inside the
   Remove old embeddings press (up to 200 documents), through an admin-only endpoint (`/embedding/legacy-cleanup`,
   at most 50 keys per call) called with the admin's own token. There is no waiting period and nothing is removed
   automatically.

## 4. Orphans: how a file is judged

A candidate is only a file made for an assistant (never an ordinary library file):

```
 file record
   |-- data.astp set and that assistant is mentioned nowhere ......... assistantGone
   |-- tag drive-integration / data.originalFileId ................... drive
   |-- data.isScrapedContent / data.sourceUrl ........................ website
   |-- key starts with a group id .................................... group
   `-- anything else ................................................. NEVER a candidate
 images and videos ................................................... never handled
```

Then the retention sweep's own decision engine (`Sweeper.decide`, reused) plus two guards:

```
 changed in the last 14 days ............................ keep ("too new": an editor uploads before saving)
 used in a chat within the admin's retention days ........ keep (with no days set there is no recency check)
 key, content key or hash appears in ANY assistant,
   layered assistant or group record (any version) ...... keep ("in use")
 document has a migration record ........................ keep (the migration owns it)
 never processed (no pointer) ........................... remove the upload only
 other uploads or other principals share the content .... remove this upload only; content stays
 last reference and NO embeddings ....................... remove upload AND content
 last reference but content has embeddings .............. keep, and report it
```

Dev, read-only, 907 candidates examined in 36 s:

| Kind | Removable | In use | Embeddings remain | Too new |
|---|---|---|---|---|
| drive | 11 | 455 | 10 | 0 |
| website | 73 | 309 | 17 | 17 |
| group | 7 | 6 | 2 | 0 |
| assistantGone | 0 | 0 | 0 | 0 |

## 5. Who may delete what

For these assistant-made copies an admin may delete: nobody browses them, the owner is often a bot or a group, and the
rules above protect anything shared or used. For ordinary library uploads an admin may not: that is the owner's data,
and the normal delete already handles it with the owner's own permission. So the cleaner never lists ordinary uploads,
needs the admin check, a typed confirmation and a re-check on the server at the moment of deleting, and logs one
`ORPHAN_DELETE` line per file.

## 5b. Automatic deletion of unused chat attachments (Document Retention)

```
 admin panel > Configurations > Document Retention        (admin config "documentRetention": isActive, days)
        |  saved as sent; OFF until an admin turns it on; the only source of "how long"
        v
 daily sweep (rag/retention.py, 4 AM UTC) reads it on every run
        |-- off / missing / unreadable ........ does nothing
        `-- on: for each chat-only attachment (marked `ephemeral` at upload: RAG off, no assistant, no group, not media)
              last use  = lastAccessedAt on the document's CONTENT row in the hash-files table
              due       = (later of the upload time and last use + 1 day) + the CURRENT days
                          (the +1 day covers the stamp moving at most once a day; a change to the days applies to old files)
              then the same safety rules as everything else: used by an assistant -> keep; embeddings exist -> keep;
              shared with others -> remove only this upload; last copy -> remove the upload and its text
 A manual run with {"dryRun": true} only logs the plan.
```

One record of when a document was last used: `lastAccessedAt`, on the CONTENT row (id = sha256), whichever key reached
it. Writers: the chat service (`touchLastAccessed` in `datasources.js`, for upload keys and for the content keys an
assistant holds) and dual retrieval (`mark_sources_accessed`). Each moves it forward at most once a day. Readers: the
retention sweep and the migration inventory. Files uploaded before the `ephemeral` marker existed are never swept.

## 6. Not covered, on purpose

- Embeddings of orphans. Removing them needs the owner's access in the existing delete path, and a wrong delete there
  cannot be undone. They are counted and shown so the size of the problem is visible.
- Conversations that attached a file by key. These categories are assistant-bound, so the risk is low, but a saved chat
  that used one would lose the attachment.
- Images and videos.



## 7. Rolling back a migrated document

Rollback only moves pointers back (and, for assistants, the document ids saved in them). It never deletes anything, so
it is safe to test.

```
 Move references (forward, for comparison)           doc#global/X   status: built
   1. copy access: every reader of global/X is added to global2/X (never overwrites a grant)
   2. hash-files rows whose textLocationKey = global/X (the content row and every upload pointer),
      only if they STILL point at global/X:  textLocationKey = global2/X, migratedFrom = global/X
   3. assistants whose newest version stores global/X: the id is changed to global2/X in place
   -> doc#global/X   status: cutover, flipped = [row ids that moved], cutoverAt

 Roll back (allowed only when status = cutover; refused if cleaned or the old progress row is gone)
   for each id in doc.flipped:  textLocationKey = global2/X ?  yes -> set back to global/X
                                                               no  -> left alone (changed since), not counted
   assistants whose newest version stores global2/X: the id is changed back to global/X
   -> doc#global/X   status: rolledback, restored = [ids put back]
```

Left in place on purpose: the new embeddings (vectors, progress row, stored text), the access rows copied to the new
key, and the old embeddings.

```
                      BEFORE ROLLBACK              AFTER ROLLBACK
 file attached        hash row -> global2/X        hash row -> global/X
 to a chat            chat reads global2/X         next chat reads global/X (old embeddings)

 assistant that       sends global/X               sends global/X
 stores global/X      retrieval swaps it to        status is no longer "cutover", so no swap:
                      global2/X (looks up the      global/X (old embeddings); the lookup caches the
                      migration record, result     status for 60 s, so up to ~1 minute later on a
                      reported under global/X)     warm server

 assistant record     changed to global2/X         changed back to global/X
```

## 8. Costs, failures and stalled documents

```
 Create new embeddings (a rebuild) -> each cost is added to the document's migration row as it happens:
   embeddingCost  Nova calls, from the recorded charge (exact)           pycommon.rag.migration_cost.add_doc_cost
   ocrCost        Textract pages, from the recorded charge (exact)
   qaCalls        question-generation calls (a count; the chat service bills them and returns no cost)
 Overview > "What the migration has cost" (refreshed on demand and when documents finish):
   embeddings + OCR ........ sums over the documents' rows
   question generation ..... what the chat service BILLED the migration account, across every admin who ran it:
                             this month from the cost table, earlier months from the history table
   per document, questions . qaCalls x the average billed cost of a recent call (the migration account's latest
                             chat usage records, which carry the real tokens incl. the service's own instructions)
 Estimate before Create (rag/migration_costs.py): chunks = tokens / ~130; Nova ~800 tokens per chunk; ~1.5 question
   calls per chunk; page images at one per PDF/Office page; OCR shown as "up to" for PDFs with little text per page,
   "possible" for .docx/.pptx/.xlsx pictures, none otherwise. The constants come from dev documents and are printed
   with the numbers.
```

A document whose new embeddings stop making progress for 2 hours is marked failed by the 1-minute check (nothing is
queued for the unfinished chunks, and restarting needs an admin's login). Embedding errors reported by the pipeline are
retried automatically up to 3 times, then fail. Failed and queued documents are listed from the Overview (click the
count); each has Details and a Retry or Start now that starts it again from its original file (finished chunks are
kept). Starting by hand starts exactly the picked documents; older queued ones are not started in their place.
