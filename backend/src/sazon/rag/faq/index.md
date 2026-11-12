Fictional client content: Grupo Sazón, its pay ranges, policies and contacts are invented for this demo.

One file per question and answer, in `es/` (Spain and Mexico) and `en/`. Front matter:

- `id`: shared by the translations of an entry.
- `country`: `ES`, `MX` or `both`; country-specific answers (pay, documents, contract, insurance, licence, own vehicle) are only retrieved for candidates in that country.
- `question`: the canonical question.
- `also`: other phrasings, separated by `|`, embedded alongside the question.

The body is the answer: one or two sentences, without promises. Run `uv run sazon rag-index` after editing.
