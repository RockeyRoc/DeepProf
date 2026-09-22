# MVP-4 Acceptance Record

## Scope

MVP-4 implements the backend resource-library and textbook-RAG loop only. Desktop Library UI is intentionally outside this acceptance record.

Implemented path:

`import/upload/crawl -> SHA-256 deduplication -> parse/chunk -> offline vector index -> locatable RAG evidence`

## Delivered

- `library/` domain package with resource records, parsers, chunking, deterministic 384-dimensional hashing embeddings, injected retrieval, and a single-URL crawler.
- SQLite WAL-backed resource, document, chunk, and operation records with transaction rollback, restart persistence, draft/private defaults, activation, version import, and owner filtering.
- PDF, EPUB, DOCX, PPTX, Markdown, TXT, and HTML parsing with page/slide/logical-section locators.
- Gateway commands `library.import` and `library.crawl`, resource listing, activation, search, and chunk preview APIs.
- Contract and settings updates, audit events (`library.imported`, `library.crawled`, `library.indexed`), an explicit seed tool, and a small original course fixture with 20 labeled queries.

## Verification

- Python modules compile successfully with `compileall`.
- Manual import/search smoke test: passed; returned `document_id`, `chunk_id`, `page`, `source`, and text evidence.
- Manual API smoke test: passed for import, crawl, list, search, preview, activation, and audit events.
- Manual crawler checks: passed for allowlist/ToS denial, robots handling, response-size checks, rate limiting, and cross-domain redirect rejection.
- Fixture recall check: `20/20` expected sections retrieved within top 5.
- `npm.cmd run typecheck` (`apps/desktop`, including the updated client SDK contract): passed.
- PDF, DOCX, PPTX, EPUB, HTML, Markdown, and TXT parser coverage is present in `tests/library/test_pipeline.py`.

## Environment note

The implementation was not marked as fully pytest-green because this Windows environment prevents pytest's temporary-directory fixture from creating its `.lock` file under the configured temp root. This is an environment permission issue, not a reported assertion failure. The affected test suite uses `tmp_path`; compile checks and the manual offline smoke checks above were run successfully.

## Explicit seed

Run `python scripts/seed_library.py` to import the built-in course fixture. Runtime startup does not seed or write user library data implicitly.
