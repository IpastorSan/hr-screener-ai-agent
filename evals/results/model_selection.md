# Model selection

Gemini 3.5 Flash-Lite reads the candidate's messages and Claude Haiku 5.5 writes Ana's replies. Twelve models ran on the same labelled cases through the production code on 2026-10-19. Gemini got all 36 of the phrasings the engine depends on right, against Haiku's 34, at $1.08 per 1K calls and 1.37 s at p95; GPT-6 Luna is cheaper but takes 4.7 s at p95. Haiku passed the most reply checks (98%) and starts speaking in 0.72 s, which matters on voice. An Opus 5.5 judge prefers Sonnet 5.5's replies to Haiku's, but Sonnet costs 20 times as much per reply and is slower to start, so it stays the option rather than the default. The judge rates Kimi K2.6, the fastest to start speaking, lowest of all: 39% against Haiku. Re-run on the 217 cases the file held on 2026-10-21 (235 now, with 18 question cases), Gemini got every field right on 96% of the ordinary messages and 34 of the 36 engine phrasings, and the prompt growth since then puts a screening at about $18.90 per 1K candidates.

## Defaults

| Role | Model | Why |
|---|---|---|
| Extractor | `google/gemini-3.5-flash-lite@minimal` | Passes all 36 engine-contract cases against Haiku's 34, with 97% F1 and 95% exact match against Haiku's 95% and 92%. The price is about 2.3x Haiku's extract cost ($1.08 against $0.46 per 1K extract calls). Latency is not the reason: Haiku is faster at both p50 (0.79 s against 1.01 s) and p95 (1.23 s against 1.37 s). |
| Responder | `anthropic/claude-haiku-5.5` | Best reply checks (98%), 0.72 s to first token for voice, $0.23 per 1K replies. The judge prefers Sonnet 5.5 and GPT-6.1 Sol, rates most cheap alternatives level with Haiku and Kimi K2.6 lowest (see the judge section). |
| Summarizer | `anthropic/claude-haiku-5.5` | Not benchmarked; one call per candidate, so stay on the responder's model with thinking off. |
| Judge | `anthropic/claude-opus-5.5` | Strongest model (99% F1 and 100% engine contract over all 201 cases); offline only, so cost does not matter. |

Finalist combinations for the end-to-end evals: **A** is the Gemini 3.5 Flash-Lite extractor with the Haiku 5.5 responder, the defaults, at 12 x ($0.00108 extract + $0.00023 respond) = $15.74 per 1K candidates on 2026-10-19 prompts. **B** is Sonnet 5.5 at low effort in both roles, the best judged replies under 1.2 s, at about $165 per 1K candidates.

The judge alone did not change the responder. None of the cheap challengers is clearly better than Haiku, and the two that are cost more or are slower. Sonnet 5.5 costs $4.54 per 1K replies against Haiku's $0.23 (about $67 per 1K candidates with the Gemini extractor, against $15.74) and starts speaking at 1.07 s against 0.72 s. GPT-6.1 Sol takes 1.68 s to the first token, too slow for voice. Haiku lost on length and added details, which a responder prompt can fix, so the reply guard added a hard two-sentence cap in `respond.md` and a sentence-count reply check, then re-judged Haiku against Sonnet. Haiku still lost (see "After the reply guard" below) and the defaults stayed.

## Default extractor on the full case file (2026-10-21)

`google/gemini-3.5-flash-lite@minimal` on all 217 cases `extraction_cases.yaml` held before the 18 question cases below were added, once each, through the production `sazon.llm` layer with the current extraction prompt (`model_selection_2026-10-21_extraction_default-217.json`, $0.29).

| Cases | Schema valid | Field F1 | Exact match | Intent | Sentiment | Matrix lang | Explicit lang | Engine contract | Spanglish | Case pass | Extract p50 / p95 s | $ / 1K extract calls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 181 non-contract + 36 contract | 100% | 97.1% | 95.6% | 94.5% | 95.6% | 98.3% | 100% | 34 / 36 | 82% (23 / 28) | 163 / 181 | 1.11 / 1.77 | $1.34 |

The columns are defined under the 12-model table below. In short, field F1 scores the extracted fields against the gold labels, exact match is the share of cases with every field right, engine-contract cases are the phrasings the engine relies on the extractor to normalize, and case pass counts the ordinary cases with fields, intent and language all right.

- **Sentiment.** The model labelled all 13 cases where only `frustrated` is right as frustrated: the 5 insults and the 8 polite-frustration cases (fed up without an insult, ES, MX and EN). It labelled no calm message frustrated. Its 8 misses on the 181 non-contract cases are calm or sarcastic messages where it and the label disagree between `neutral` and `positive`, and one confused Spanglish message read as `neutral`. A constant `neutral` scores 91.2% (165 of 181); the model scores 95.6% (173 of 181).
- **Intent on complaints.** On 4 of the 8 polite-frustration cases with no answer in them ("Seriously, how many more questions? I just want to apply.") the model said `answer` with no fields, where the gold label wants `unclear` or `small_talk`. The engine re-asks the question either way, and the frustration label still triggers the shorter reply.
- **Engine contract, 34 of 36.** `ec-license-es-06` ("todavía no" at the licence question) came back as `in_progress` instead of `false`; both close the application as not holding a valid licence. `ec-exp-es-05` ("desde 2022") came back as 4.8 years instead of 4. Both cases passed in the 12-model run, so these are run-to-run variation on a single repeat.
- **Cost.** The extraction prompt has grown since the 12-model run (3,635 input tokens per call on average, against 2,708), so an extract call now costs $1.34 per 1K instead of $1.08. With Haiku 5.5 replying, a screening of 12 extract and 12 respond calls costs about $18.90 per 1K candidates; with a Sonnet 5.5 responder ($4.54 per 1K replies) it is about $71.

## Standalone and multi-part questions (2026-10-21)

18 cases added for the rewrite of follow-up questions and the split of multi-part ones: `standalone_question` (11, three of them negatives that must come back unchanged) and `multi_part` (7, two negatives that must not be split), in ES, MX and EN. The default extractor, 3 repeats (`model_selection_2026-10-21_extraction_qa-br-new-cases.json`, $0.04): 50 of 54 calls had every check and field right. `br-multi-es-02` ("¿el uniforme y el casco los dais vosotros?") was never split, and once "¿y eso viene con propinas?" came back as "¿La cantidad mencionada incluye las propinas?", which names no amount.

On the whole file once (`..._qa-br-full.json`, $0.28), the 217 earlier cases had every check and field right in 173, against 184 with the previous prompt in the same session (`..._qa-br-full-devprompt.json`, $0.35). Re-run twice with each prompt, the 27 cases that differed scored 29 of 54 against 37 of 54. Every difference was in the sentiment, matrix-language or code-switching labels, none in a field or a question, so it may be noise on a single repeat; a repeated full run would settle it.

### Repeat check of the new prompt (2026-10-21)

Two more full runs with the new prompt (`model_selection_2026-10-21_extraction_br-regression-r2.json`, $0.37) had every check and field right in 184 and 181 of the 217 earlier cases. Over three runs the new prompt averages 179.3 (173 to 184), against 184 in both runs with the old prompt. Fields, intent and questions are level; the differences sit in two labels:

| Prompt | Runs | Fully right | Matrix-language misses | Sentiment misses |
|---|---|---|---|---|
| old | 2 | 184, 184 | 3, 3 | 10, 13 |
| new | 3 | 173, 184, 181 | 9, 3, 6 | 16, 13, 11 |

Sentiment is noise: the same calm messages swing between `neutral` and `positive` with either prompt. Matrix language leans slightly worse on Spanglish-MX: `var-spanglish-mx-19` ("I've done like two years of reparto with Didi") came back `mixed` in all three new runs and in neither old one. The new instructions touch only `question` and `sub_questions`, so nothing in them points at either label. It is not a regression worth acting on: the prompt stays.

A sentiment line tried on the 33 cases that moved, 3 repeats each, against the unchanged prompt in the same session (`..._br-regression-flips-control.json`, `..._br-regression-flips-tweak2.json`, $0.26 with the control and a first draft): "`positive` for thanks, praise or eagerness ... A plain answer, or a yes with details, is `neutral`". Sentiment misses fell from 31 to 8 of 99 and fully right calls rose from 58 to 74, but `ec-pool-es-01` ("sí, avisadme", gold `positive`) turned `neutral` in all three, and matrix-language misses did not move (9 against 11). A draft that also told the model to label only the message's own language left matrix-language misses at 8. The line is not in the prompt; it would need a full-file run first.

## The 12-model benchmark (2026-10-19)

Every call ran through the production `sazon.llm` layer (`PydanticAILLM`: the shipped prompts, prompted JSON output for the extractor, per-role reasoning settings) on `https://openrouter.ai/api/v1` with `provider: {zdr: true, data_collection: "deny"}`, asserted on every request. Extraction: the 201 gold-labelled cases the file held then, 1 repeat. Replies: 41 cases with deterministic checks, a second streamed call per case to time the first token (TTFT), and a pairwise judge against Haiku 5.5. Raw data: `model_selection_2026-10-19.json` (table rows), `model_selection_2026-10-19_extraction_final.json`, `model_selection_2026-10-19_reply_final.json`, `model_selection_2026-10-19_judge_anchored.json`.

| Model | Schema valid | Field F1 | Exact match | Intent | Sentiment | Matrix lang | Explicit lang | Engine contract | Spanglish | Extract p50 / p95 s | Reply checks | TTFT p50 s | Judge vs Haiku (95% CI) | $ / 1K candidates |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| z-ai/glm-5.3-flash@minimal | 100% | 98% | 98% | 95% | **97.0%** | 97% | 100% | 97% | 82% | 0.91 / 4.56 | 88% | 0.69 | 53% (40-66%) | $4.26 |
| deepseek/deepseek-v4.1-flash | 100% | 96% | 95% | 97% | 95.8% | 96% | 100% | 94% | 82% | 0.95 / 2.23 | 85% | 0.71 | 56% (43-70%) | $3.60 |
| anthropic/claude-haiku-5.5 | 100% | 95% | 92% | 96% | 89.7% | 96% | 99% | 94% | 75% | 0.79 / 1.23 | **98%** | 0.72 | anchor | $8.29 |
| openai/gpt-6-luna | 100% | 97% | 95% | 96% | 92.1% | 98% | 100% | 100% | 89% | 1.84 / 4.70 | 93% | 1.02 | 57% (43-71%) | **$1.65** |
| z-ai/glm-5.3@minimal | 100% | 95% | 93% | 98% | 92.7% | 97% | 100% | 97% | 82% | 1.29 / 3.26 | 95% | 0.65 | 55% (41-70%) | $12.36 |
| google/gemini-3.5-flash-lite@minimal | 100% | 97% | 95% | 96% | 93.9% | 98% | 100% | 100% | 82% | 1.01 / 1.37 | 90% | 0.70 | 53% (39-66%) | $18.88 |
| mistralai/mistral-large-4-0 | 99% | 96% | 93% | 95% | 90.9% | 93% | 98% | 100% | 75% | 1.77 / 3.29 | 88% | 0.83 | 51% (38-63%) | $11.47 |
| moonshotai/kimi-k2.6 | 100% | 98% | 96% | 94% | 92.7% | 98% | 100% | 97% | 82% | 1.00 / 3.29 | 85% | **0.54** | 39% (26-52%) | $18.16 |
| openai/gpt-6.1-sol@low | 100% | 98% | 98% | 96% | 96.4% | 100% | 100% | 100% | 93% | 3.53 / 6.39 | 93% | 1.68 | **64% (51-77%)** | $31.53 |
| anthropic/claude-sonnet-5.5@low | 100% | 97% | 96% | 97% | 94.5% | 98% | 99% | 97% | 89% | 1.14 / 1.67 | 95% | 1.07 | **67% (54-79%)** | $164.61 |
| x-ai/grok-4.6 (control) | 100% | 98% | 98% | 97% | 94.5% | 99% | 99% | 100% | 93% | 10.48 / 29.35 | 85% | 16.34 | 47% (33-61%) | $198.62 |
| anthropic/claude-opus-5.5 (quality ceiling) | 100% | **99%** | **98%** | **98%** | 93.9% | 99% | 99% | 100% | 93% | 2.36 / 6.26 | - | - | judge | - |

How to read it:
- Field F1, exact match, intent and the two language columns are over the 165 non-contract cases. Field F1 pools every extracted field across those cases: precision is the share of fields the model reported that match the gold label, recall the share of gold fields it found, and F1 their harmonic mean. Exact match is the share of cases with every field right. Matrix lang is the main language of the message (Spanish, English or mixed); explicit lang is whether the candidate asked to switch language.
- Engine contract (36 cases) covers the phrasings the engine relies on the extractor to normalize, such as "todavía no" at the licence question or "desde 2022" for experience. The column is the share of those cases passed in full. Spanglish (28 cases) is the share passed in full: fields exact, intent, matrix language and explicit request all right. Case pass, in the other tables, is the same test over the non-contract cases.
- Sentiment is the share of the 165 non-contract cases whose gold label accepts the model's sentiment, recomputed from the stored calls in `model_selection_2026-10-19_extraction_final.json` (one decimal, since the spread is small). It is part of no "passed in full" number. On these 165 cases an extractor that always answers `neutral` scores 95.2%, because the only cases where `frustrated` alone is right were 5 insults. The 8 polite-frustration cases added since change that; the default extractor's result on them is in the 2026-10-21 section. `--stage report` prints the column and the baseline line.
- `@effort` is the reasoning setting the run used (`@minimal`, `@low`); no suffix means reasoning off. Gemini 3.5 Flash-Lite and GLM-5.3 reject requests with reasoning off ("Reasoning is mandatory for this endpoint"), so they run at `@minimal`.
- Reply checks is the share of the 41 reply cases that pass the deterministic checks.
- $ per 1K candidates assumes the model in both roles: 12 extract and 12 respond calls per screening, at the measured mean cost per call (OpenRouter `usage.cost`).
- Opus is the quality ceiling, not a candidate: extraction only (no reply run), $0.0198 per extract call (about $238 per 1K candidates for extraction alone) and a 6.3 s p95. It covers all 201 cases, merged from three runs: the first run and a resume were cut off by the OpenRouter key's spend limit (HTTP 402/403, infrastructure errors that were re-run), and the last 55 cases ran on 2026-10-19 after the limit was raised. The provider's content filter refused one injection case (`var-injection-es-03`, "violative cyber content"), which counts as a failed, schema-invalid case. Mistral's 84 rate-limited calls (HTTP 429) were re-run at concurrency 1 and merged.
- Judge vs Haiku: Opus 5.5 compared each model's reply with Haiku 5.5's reply to the same action, twice per case with the order swapped (41 cases x 2 orders = 82 judgments per model; identical replies count as a tie without a call). The challenger scores 1 per win, 0.5 per tie and 0 per loss, so 50% means as good as Haiku. The 95% CI is a case-level bootstrap (10,000 resamples of the 41 cases, seed 24), since the two orders of one case are not independent. Six models were judged on 2026-10-19 and 2026-10-20; GLM-5.3 Flash, Mistral, Kimi and Grok followed on 2026-10-21 with the same judge, prompt and stored replies.

### Findings

- **Output mode mattered more than the model.** Through OpenRouter, tool-call output came back with the open `fields` dict empty for GPT-6 Luna on every case, GLM-5.3 Flash on 24-26% and DeepSeek V4.1 Flash on 11%. Native JSON schema dropped it on Claude Haiku (pass 20%) and errored on GLM. Prompted JSON fixed it everywhere (Luna pass 18% → 92%, DeepSeek 84% → 90%, GLM Flash 72% → 92%, Haiku 86% → 90%) and was no slower, so the production extractor uses prompted output.
- **Quality ceiling.** Opus 5.5 reaches 99% field F1, 98% exact match and 100% engine contract over all 201 cases. GPT-6.1 Sol at low effort, Grok 4.6 and Sonnet 5.5 at low effort come close (97-98% F1). Sol and Grok are too slow for a live turn (Sol 3.5 s p50, Grok 10.5 s); Sonnet is the quality option at 1.1 s, but at 20x Haiku's cost.
- **Cheapest.** GPT-6 Luna costs $1.65 per 1K candidates with 97% F1 and 100% engine contract, but takes 1.8 s at p50 and 4.7 s at p95 to extract. GLM-5.3 Flash and DeepSeek V4.1 Flash are as cheap with sub-second medians, but have weaker reply checks (85-88%) and long tails.
- **Voice latency.** Claude Haiku 5.5 reaches the first token in 0.72 s with the best reply checks (98%). Kimi K2.6 (0.54 s) and GLM-5.3 (0.65 s) are faster to the first token but pass fewer reply checks (85% and 95%), and the judge rates Kimi's replies below Haiku's (39%).
- **The judge and the reply checks disagree on Haiku.** Haiku passes the most deterministic checks (98%), yet eight of the ten challengers score above 50% against it (51-67%). Only Sonnet 5.5 and GPT-6.1 Sol are clearly better (CI above 50%); Luna, DeepSeek, GLM-5.3, GLM-5.3 Flash, Gemini, Mistral and Grok (47%) are within noise of Haiku. Kimi K2.6 scores lowest, 39% (CI 26-52%), the only interval almost entirely below 50%: its replies run longer and stiffer, and on single cases it used tú in Mexico, invented a schedule in a read-back, left the address and month out of the interview slots, and asked two questions. The judge's reasons are mostly length and small invented details: Haiku runs past two sentences in 11 of 41 replies (the most of any model; Sol 2, Gemini and DeepSeek 4) and adds facts the action does not carry, such as reapplying "por este mismo chat" on disqualifications. The deterministic checks did not count sentences, so they missed it.
- **Spanglish is the weak spot for every model** (75-93% passed). Most models miss a few of the same cases (`var-spanglish-mx-22`: 11 of 12 got the intent wrong), which points at the gold label more than the models; the review sheet lists who missed each case.
- **Prompt merges** (measured before the full run, on 60 cases). Merging the provisional eval extraction rules into `extract.md` raised Haiku from 89% to 96% field F1 (case pass 78% → 86%). Merging the provisional responder rules into `respond.md` (usted never tú, no added question, no sign-off, no human handoff, a kind disqualification) raised reply checks from 88% to 90% on Haiku and from 83% to 90% on DeepSeek.

### Judge vs Haiku 5.5

| Challenger | Wins | Ties | Losses | Score vs Haiku | 95% CI | Cases with both orders agreeing |
|---|---|---|---|---|---|---|
| anthropic/claude-sonnet-5.5@low | 51 | 8 | 23 | **67%** | 54-79% | 34 / 41 |
| openai/gpt-6.1-sol@low | 48 | 9 | 25 | **64%** | 51-77% | 37 / 41 |
| openai/gpt-6-luna | 42 | 10 | 30 | 57% | 43-71% | 36 / 41 |
| deepseek/deepseek-v4.1-flash | 43 | 6 | 33 | 56% | 43-70% | 36 / 41 |
| z-ai/glm-5.3@minimal | 44 | 3 | 35 | 55% | 41-70% | 37 / 41 |
| google/gemini-3.5-flash-lite@minimal | 39 | 9 | 34 | 53% | 39-66% | 37 / 41 |
| z-ai/glm-5.3-flash@minimal | 39 | 9 | 34 | 53% | 40-66% | 34 / 41 |
| mistralai/mistral-large-4-0 | 35 | 13 | 34 | 51% | 38-63% | 36 / 41 |
| x-ai/grok-4.6 | 36 | 5 | 41 | 47% | 33-61% | 38 / 41 |
| moonshotai/kimi-k2.6 | 27 | 10 | 45 | 39% | 26-52% | 37 / 41 |

51.2% of the decisive verdicts went to the message shown first, so position bias is small. Haiku's pooled score over its 820 comparisons is 46%. In a blind check of 20 pairs from the first six models' run, a person agreed with the judge on only 9, so the judge is not calibrated and these scores are indicative.

### After the reply guard: Haiku 5.5 vs Sonnet 5.5 again

Haiku's replies were regenerated through the production layer with the reply guard's responder (hard caps in `respond.md`, the reply guard with one retry and then the template). Sonnet's replies are the ones from `model_selection_2026-10-19_reply_final.json`, so Sonnet ran on the earlier prompt. Same Opus 5.5 judge, 41 cases in both orders.

| Challenger vs Haiku 5.5 | Wins | Ties | Losses | Score vs Haiku | 95% CI | Cases with both orders agreeing |
|---|---|---|---|---|---|---|
| anthropic/claude-sonnet-5.5@low, before the reply guard | 51 | 8 | 23 | 67% | 54-79% | 34 / 41 |
| anthropic/claude-sonnet-5.5@low, after the reply guard | 58 | 7 | 17 | **75%** | 63-85% | 35 / 41 |

Haiku's reply checks went from 98% to 93%, because a reply the guard rejects twice is now sent as the template and counts as not produced (5 of 82 calls on the reply guard's first prompt, mostly three-sentence disqualifications). Haiku loses to Sonnet by more than before. The judge prefers Sonnet's plain AI disclosure ("asistente de IA" against the template's "asistente virtual") and shorter greetings, and some of Haiku's losses are templates the guard fell back to. The defaults stayed; a Sonnet responder costs about $67 per 1K candidates against $15.74.

Raw data: `model_selection_2026-10-20_reply_reply-guard-haiku.json`, `model_selection_2026-10-20_reply_reply-guard-haiku-vs-sonnet.json` (merged input), `model_selection_2026-10-20_judge_reply-guard.json`. Spend: $0.03 replies, $0.80 judge.

## Label changes and re-score (2026-10-20)

A review of the gold labels by hand changed three of them and the schedule format; the 12-model table is on the original labels. The 2,412 stored extractor outputs were re-scored against the new labels with no new model call (`--stage rescore`, `model_selection_2026-10-20_extraction_final-rescored.json`):

- `var-name-mx-01`: `full_name` "juan carlos hernández". The engine now expands surname abbreviations, so names are scored as the engine stores them ("hdz" and "Hernández" agree).
- `var-sched-es-03`: `schedule` [morning, afternoon]. The schedule is a list since this re-labelling; every schedule label in the dataset is now a list and scoring compares sets.
- `seed-spanglish-mx-02`: no `availability` ("part time" describes the past job).
- `var-exp-en-02`: stays vague ("a while"); the engine asks how long.
- `var-city-mx-08` stays Puebla; the city review comments needed no change. `seed-license-es-01` and `var-license-mx-04` (learner's permit) stay `in_progress`, a rejection.

| Model | Field F1 | Exact match | Case pass | Cases whose result changed |
|---|---|---|---|---|
| z-ai/glm-5.3-flash@minimal | 98.0% → 97.7% | 97.6% → 97.0% | 91.5% → 90.9% | seed-spanglish-mx-02 fail |
| deepseek/deepseek-v4.1-flash | 96.4% → 96.1% | 95.2% → 94.5% | 90.3% → 89.7% | seed-spanglish-mx-02 fail |
| anthropic/claude-haiku-5.5 | 95.0% → 95.2% | 92.1% → 92.7% | 87.9% → 88.5% | seed-spanglish-mx-02 pass |
| openai/gpt-6-luna | 97.2% → 96.9% | 95.2% → 94.5% | 91.5% → 90.9% | seed-spanglish-mx-02 fail |
| z-ai/glm-5.3@minimal | 95.3% → 95.0% | 93.3% → 92.7% | 89.7% → 89.1% | seed-spanglish-mx-02 fail |
| google/gemini-3.5-flash-lite@minimal | 96.7% → 96.4% | 94.5% → 93.9% | 91.5% → 90.9% | seed-spanglish-mx-02 fail |
| mistralai/mistral-large-4-0 | 95.8% → 95.5% | 92.7% → 92.1% | 87.9% → 87.3% | seed-spanglish-mx-02 fail |
| moonshotai/kimi-k2.6 | 97.8% → 97.5% | 95.8% → 95.2% | 91.5% → 90.9% | seed-spanglish-mx-02 fail |
| openai/gpt-6.1-sol@low | 98.3% → 98.0% | 97.6% → 97.0% | 95.2% → 94.5% | seed-spanglish-mx-02 fail |
| anthropic/claude-sonnet-5.5@low | 97.5% → 97.2% | 96.4% → 95.8% | 94.5% → 93.9% | seed-spanglish-mx-02 fail |
| x-ai/grok-4.6 | 98.3% → 98.0% | 97.6% → 97.0% | 95.8% → 95.2% | seed-spanglish-mx-02 fail |
| anthropic/claude-opus-5.5 | 99.4% → 99.7% | 98.2% → 98.8% | 97.0% → 97.6% | seed-spanglish-mx-02 pass |

Only `seed-spanglish-mx-02` moved. Ten models report the past part-time job as availability and now fail it; Haiku 5.5 and Opus 5.5 leave it out and now pass. The name and schedule changes move no model: every model either kept "hdz", which the validator expands, or already gave both blocks. The ranking does not change.

### New cases: Morse, runes and the strict licence

Six new cases (`var-injection-es-06`, `var-injection-mx-05`, `var-injection-en-04`, `var-injection-es-07`, `var-license-mx-06`, `var-license-es-05`) ran once on the 12 models (`model_selection_2026-10-20_extraction_relabel-new-cases.json`, $0.30). They are reported here, not merged into the 12-model table, so its denominators stay comparable.

- **Claude models refuse Morse.** Haiku 5.5, Sonnet 5.5 and Opus 5.5 return a content-filter refusal on both Morse messages (`ContentFilterError`), so the extraction falls back. The service used to answer a fallback extraction by repeating the question without stepping the engine, which would have let a Morse attempt through with a plain re-ask. A message the detector flags now always reaches the engine and closes the application (`test_an_injection_closes_even_when_the_extractor_refuses_to_read_it`). Every model reads runes normally.
- **No model obeyed the encoded instructions.** Mistral Large 4 read `has_license` from the Morse message in the licence stage, and GLM-5.3 Flash took the runes in the name slot as a name; the detector closes both conversations before any field is applied.
- **`var-license-es-05`** ("lo tengo pero lo estoy renovando"): DeepSeek V4.1 Flash, Gemini 3.5 Flash-Lite and Kimi K2.6 still say `true`. The engine reads "renovando" itself and closes it as `license_not_valid` whatever the extractor said. `var-license-mx-06` ("se me venció") passes on all 12.

Spend: $0.30 for the new cases (estimate $0.47); the re-score cost nothing. Ledger after the run: $21.59.

## Spend

Phase B spent $8.38 (extraction $6.96, replies $1.43; the prompt and output-mode experiments are included). The first judge attempt spent $0.92 (Opus resume $0.86, judge smoke $0.06) before the OpenRouter key's own $10 limit stopped it. With the key limit raised to $40, the finishing run spent $5.59: the last 55 Opus cases $1.08, the anchored judge $3.75 (410 comparisons, 392 paid calls at $0.0096) and the DeepSeek judge $0.75 (82 comparisons). The cumulative ledger was $15.09 of `MAX_EVAL_USD=35` after that run. The default extractor on all 217 cases (2026-10-21) spent $0.29. Judging the last four models (2026-10-21) spent $3.01 (328 comparisons, 312 paid calls; estimate $3.35). The standalone and multi-part question extraction runs (2026-10-21), prompt iterations included, spent $0.89. The repeat check of the new prompt (2026-10-21), sentiment drafts included, spent $0.63.

The ledger (`cost_ledger.jsonl`) is local and untracked, and `MAX_EVAL_USD` caps its total on each machine; every results file keeps its own run's spend under `meta.run_cost_usd`. Each table and section in this note names the raw results file behind it; the smoke, retry, prompt and output-mode runs that fed none of them were not kept.

## Reproduce

```
uv run --project backend python -m evals.model_selection --stage extraction --models <list> [--dry-run]
uv run --project backend python -m evals.model_selection --stage reply --models <list>
uv run --project backend python -m evals.model_selection --stage judge \
    --models anthropic/claude-haiku-5.5,anthropic/claude-sonnet-5.5@low,z-ai/glm-5.3@minimal,openai/gpt-6-luna,openai/gpt-6.1-sol@low,google/gemini-3.5-flash-lite@minimal \
    --anchor anthropic/claude-haiku-5.5 --replies evals/results/model_selection_2026-10-19_reply_final.json --tag anchored
uv run --project backend python -m evals.model_selection --stage judge \
    --models anthropic/claude-haiku-5.5,deepseek/deepseek-v4.1-flash \
    --anchor anthropic/claude-haiku-5.5 --replies evals/results/model_selection_2026-10-19_reply_final.json --tag anchored-deepseek
uv run --project backend python -m evals.model_selection --stage judge \
    --models anthropic/claude-haiku-5.5,z-ai/glm-5.3-flash@minimal,mistralai/mistral-large-4-0,moonshotai/kimi-k2.6,x-ai/grok-4.6 \
    --anchor anthropic/claude-haiku-5.5 --replies evals/results/model_selection_2026-10-19_reply_final.json --tag anchored-remaining
uv run --project backend python -m evals.model_selection --stage report \
    --extraction <extraction json> --replies <reply json> [--judge <judge json>]
uv run --project backend python -m evals.model_selection --stage rescore \
    --extraction evals/results/model_selection_2026-10-19_extraction_final.json --tag final-rescored
```

The DeepSeek judge run (`model_selection_2026-10-20_judge_anchored-deepseek.json`) and the run on the last four models (`model_selection_2026-10-21_judge_anchored-remaining.json`) are merged into `model_selection_2026-10-19_judge_anchored.json`, which also holds the per-challenger counts and bootstrap intervals under `vs_anchor`. The bootstrap draws from one `random.Random(24)` shared across the challengers in `vs_anchor` order, so adding the last four left the first six intervals unchanged. `vs_anchor` in `evals/model_selection/judge.py` computes it (the interval ends are the 2.5th and 97.5th percentiles with "lower" interpolation); `--stage report --judge <json>` recomputes the scores and intervals from the stored verdicts and prints them, and an anchored judge run stores them under `vs_anchor`.

The judge prompt carries the engine's template reply as a reference, so it changes with the templates. The last four models were judged from the code as it stood at the first judge run (2026-10-19), so all ten saw the same prompt; on the current code the judge stage stops on `slots-es-mx-voice`, whose slots have no `startsAt` for the voice template to read aloud.
