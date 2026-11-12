Fictional client content: Grupo Sazón, its documents, pay, policies, addresses and contacts are invented for this demo.

Company documents for retrieval, in `es/` (Spain and Mexico) and `en/`, one file per document and language with the same file name. Front matter:

- `id`: shared by the translations of a document; country variants end in `-es` or `-mx`.
- `title`: what recruiters see in the source line, with the section (`Manual del repartidor › Seguridad en la moto`).
- `country`: `ES`, `MX` or `both`; a country's documents are only retrieved for candidates in that country.
- `language`: `es` or `en`, matching the folder.
- `updated`: the date of the last content change.

`#` is the document title; `##` and `###` open sections, and each section becomes one or more passages of up to 200 words. Keep the facts consistent with the Q&A in `../faq/`, the job postings and the privacy notice on the web; the Q&A wins for short answers and these documents add the detail. `locations-*.md` list every kitchen in `agents/riders/data/service_areas.json` (checked by `tests/test_rag_docs.py`). Run `uv run sazon rag-index` after editing.
