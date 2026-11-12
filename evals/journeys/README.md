# Candidate journeys

Scripted candidate journeys played against the real `ScreeningService`, in process, for the written chat in Spain and Mexico, and calls typed as the speech to text writes them (`channel: voice`). Each journey is one or more chat sessions with the same candidate. Every step is checked deterministically against Ana's action, the conversation state and the reply.

```
uv run --project backend python -m evals.journeys [--agent a] [--group g] [--id j1,j2] [--offline]
    [--repeat N] [--combo A] [--max-usd 2] [--concurrency 4] [--keep-db] [--dry-run]
make journeys                      # offline
make journeys ARGS="--group seed"  # real models
```

- `--offline`: no model and no cost. The keyword extractor reads each message as the answer to the pending field, replies are the templates, `persona` steps use the scripted candidate, and there is no FAQ retrieval. `offline_say` and the `live:` expectations exist because of this.
- Real models: the product models of combo `A` (`evals/e2e/run.py`) through OpenRouter, every call settled in the eval ledger. Needs `MAX_EVAL_USD`, which caps the ledger's cumulative total (`evals/results/cost_ledger.jsonl`). `--max-usd` caps this run. `--dry-run` lists the journeys and projects the cost.
- FAQ retrieval needs the embedding model and the corpus vectors in `backend/.cache`, which git ignores, so a fresh checkout or worktree has none. Without them every question gets the unknown answer, so a run with real models (journeys and `evals.e2e` alike) stops before its first call and says so. Build the cache once with `cd backend && uv run sazon rag-index` (downloads the model, about a minute), or copy `backend/.cache` from a checkout that has it. An offline e2e run warns and goes on.
- Each agent's journeys run screening for it: the riders' in `cases/`, another agent's in `cases/<agent>/` (the teachers' in `cases/teachers/`), one agent after the other; `--agent` runs one. A journey's run is named `<group>.<id>`, prefixed with its agent when it is not the riders'.
- Each journey runs on its own in-memory SQLite database, with the agent's interviewers seeded (its `recruiters` file) and a fake calendar, so no Google calendar is touched and no invitation is sent. A journey's `scheduling` can offer only one interview mode, or fill the recruiters' calendars on some days or parts of them (`busy`: video only; in-person times are the locations'). The configured `DATABASE_URL` is never used. Nothing is left behind except the report. `--keep-db` writes each journey's database into the run folder for debugging.
- The clock starts at Monday 19 October 2026, 10:00 in Madrid. It moves 20 seconds per candidate turn and jumps by a session's `after`.

Each run writes `evals/results/journeys/<timestamp>/`, which git ignores:

- `summary.md`: one row per journey with its status, turns, cost and first failed check.
- `<group>.<id>.json`: every failure and the full transcript. Each candidate turn has the text sent and the extraction. Each Ana turn has the reply, widget, action, FAQ sources, fallback flag, invariant results and a state snapshot.

The exit code is 1 when a journey fails or the run is stopped. An `xfail` journey that fails is reported as `xfail` and does not count against the run. One that passes is reported as `XPASS`, which means its `xfail` can be removed. Offline, a journey that only fails on `live:` expectations or on how a model reads a message reports `XPASS` too, so judge an `xfail` by a run with real models.

## Format

`cases/<group>.yaml` holds a list of journeys. The file name is the group.

```yaml
- id: reapply_after_no_license        # unique across groups
  description: What the journey covers.
  country: ES                         # the posting's country: ES or MX
  location: es-mad-centro             # optional: a posting at this location id
  context: {locale: en-GB}            # optional overrides of the chat context
  register: tu                        # optional; default tu in ES, usted in MX
  channel: voice                      # optional: a call (spoken replies, no card); never tap
  xfail: "batch 2: ..."               # optional: known to fail until this fix lands
  scheduling:                         # optional: the interview world (default: both modes, all free)
    modes: [onsite]                   #   only these modes on offer (no recruiters, or no in person)
    busy: ["2026-10-20"]              #   recruiters' calendars full these days, or `all`: no video time,
                                      #   or a window of a day: "2026-10-27 00:00-11:50" (local)
  candidate:                          # for persona steps: the e2e persona truth format
    language: Spanish from Spain, casual.
    truth: {full_name: ..., phone: "+34...", location_id: es-mad-centro, ...}
  sessions:                           # or `steps:` for a single session
    - steps: [...]
    - after: 3d                       # clock jump before this session (d, h, m)
      restart: true                   # optional: opened with the previous chat's restart button
      country: MX                     # optional: through a country-wide posting of this country
      register: mixed                 # optional: this session's register; mixed skips the check
      steps: [...]
```

Sessions share the database, so a later session that gives the same phone or email meets the duplicate check. A session opens as a new chat (a new tab, another posting); with `restart: true` it opens through the previous session's "Empezar de nuevo", which closes an application still open there first.

A step has exactly one move:

- `say: "texto"`: a typed message. `offline_say:` replaces it offline, where the keyword extractor can't read free text (for example `"2026-10-26"` for "el lunes que viene").
- `tap: yes`: a widget answer, as the web sends it. It can be an option value (`yes`, `no`, `video`, `onsite`, a location id), `slot:1` for the n-th offered time, a list for a multi-select, or `{country: MX, number: "5512345602"}` for the phone picker. A tap on a value the widget doesn't show fails the `tap` check, because the web couldn't send it.
- `persona: "brief"` with `until: {<expectations>}`: the e2e simulator plays the candidate until the expectations hold. It takes at most `max_turns` turns (default 12) and fails `until` if they never hold. The candidate gets the journey's `candidate.truth`, merged with the step's `facts`. Offline, the scripted simulator answers from that truth.

`expect:` checks the state after the step. Every key is optional:

| Key | Holds when |
|---|---|
| `asks` | The pending question is this field (`full_name`, `phone`, `email`, `has_license`, `city`, ...), or `consent`, `confirm`, `mode`, `slot`, `talent_pool`, `pause`, `cancel`, `move` (moving a booked interview to another location), `none` |
| `action` | Ana's action kind (`ask_field`, `answer_question`, `disqualify`, `confirm_booking`, ...) |
| `outcome` | `qualified`, `disqualified`, `abandoned`, `declined_consent`, `withdrawn` or `in_progress` |
| `reason` | The disqualify reason (`no_license`, `out_of_area`, ...) |
| `register` | `tu` or `usted`: a Spanish reply has no form of the other register |
| `language` | `es` or `en`, both for the action and for the reply text |
| `booked`, `mode` | There is an interview booking; its mode is `video` or `onsite` |
| `interviewer` | Who runs the booked interview: the recruiter's name by video, the location manager's in person |
| `resumed` | The session carried on an earlier application (true) or is a fresh one (false) |
| `faq_source` | The reply cites this FAQ id (`privacy`), each of a list of ids, `any` FAQ, or `none` |
| `reply_matches`, `reply_not_matches` | A regex, matched case-insensitively against the reply |
| `card_matches` | A regex over the summary card's rows, written `Label: value; Label: value` |
| `closed` | The reply ended the chat |
| `ui` | The widget under the reply: `quick_replies`, `phone`, `slots`, `confirm`, `booking`, `none` |
| `live: {...}` | Any of the above, checked only with real models |

Every Ana reply also gets four checks:

- `reply_checks`: the product's own reply checks (`llm/reply_checks.py`). Fixed template replies skip it.
- `register`: the journey's register.
- `one_question`: at most one question mark.
- `no_internal_location`: no internal location names such as "Sazón Madrid Centro". FAQ answers are exempt.

## Limits

- Offline, the keyword extractor only covers what a journey can script: one field per message, with the value written the way the validators accept it. Anything that depends on how a model reads a message has to be a `live:` expectation, or a step that runs only with real models.
- With real models, an expectation that names the next question can fail when the extractor reads more than one field from a message ("2 años en Glovo" fills the experience and the platforms). Script one fact per message, or use `persona` steps with `until`.
- With real models, the model writes the replies and the simulator picks the candidate's words, so a journey can pass or fail from run to run. Run it with `--repeat N` before trusting one result.
- No nudges or abandonment timers. A `channel: voice` journey runs the service as a call, typed (no ElevenLabs, no speech to text), so it covers what Ana says by voice but not the audio. Use a session's `after` to model time passing between sessions.
