# Epic 12 Story Pack

Epic: Sales Conversation Persona ("Николай")

This story pack is implementation-ready and includes, per story:
- scope boundaries (in/out)
- implementation notes grounded in the architecture + project-context rules
- test requirements (unit / integration)
- automated E2E + manual verification
- completion gates (100% coverage, ruff clean, secrets / `telegram_file_id` never logged)

**One PR per story.** Each story is a self-contained branch + PR mapping 1:1 to the BMAD `create-story → dev-story → code-review → PR` cycle.

Implementation order follows the dependency graph (12.01 is the foundation and blocks every later story; 12.02 / 12.03 / 12.05 are parallel after 12.01; 12.02b chains after 12.02; 12.05b and 12.05c both chain after 12.05):

```
12.01 sales DB schema + repos (state, services, client_materials, followup_queue)   ← foundation, blocks all
  ├── 12.02 service commands + /sales_state (bot_gateway)                            (parallel after 12.01)
  │     └── 12.02b NL operator dialog for service management                         (after 12.02)
  ├── 12.03 SalesPersonaAnswerer greeting + intent scoping (always-on gate)          (parallel after 12.01)
  └── 12.05 autonomous client materials dispatch (/material + selector + endpoint)   (parallel after 12.01)
        ├── 12.05b KB-upload → automatic client materials analysis                   (after 12.05)
        └── 12.05c KB-upload → automatic services extraction                         (after 12.05; ∥ 12.05b)
              12.04 pricing turn — KB-first, escalate-if-unknown                     (after 12.02 + 12.03)
              12.06 service-list + concept-explainer                                  (after 12.02)
              12.07 date-proposal turn via Epic 11 calendar                          (after 12.03)
              12.08 proactive +1d follow-up (scheduler job)                          (after 12.03)
                    12.09 pipeline wiring + always-on activation + e2e signoff       (last; depends on all)
```

## Story list
- `story-12-01-sales-db-schema-and-repositories.md`
- `story-12-02-service-commands-and-sales-state.md`
- `story-12-02b-natural-language-service-management.md`
- `story-12-03-sales-persona-answerer-greeting-and-intent.md`
- `story-12-04-pricing-turn-kb-first-escalate-if-unknown.md`
- `story-12-05-autonomous-client-materials-dispatch.md`
- `story-12-05b-kb-upload-automatic-client-materials-analysis.md`
- `story-12-05c-kb-upload-automatic-services-extraction.md`
- `story-12-06-service-list-and-concept-explainer.md`
- `story-12-07-date-proposal-turn.md`
- `story-12-08-proactive-followup.md`
- `story-12-09-pipeline-wiring-and-e2e-signoff.md`

## Three input paths for services
The `services` table can be populated three ways — all writing through `ServicesRepository`:
1. **Slash commands** (12.02) — operator's explicit one-by-one path.
2. **Natural-language operator dialog** (12.02b) — operator NL → LLM classifier → same handlers as the slash commands. DRY shared internal helpers.
3. **Automatic LLM extraction from `/kb_add` uploads** (12.05c) — every KB upload runs a `services_extractor` post-hook (in parallel with 12.05b's materials analyzer); idempotent on `(project_id, name)` — never overwrites a manually-crafted description. Confidential uploads never contribute.

## Always-on activation
There is no project-level enable for sales. The `SalesPersonaAnswerer` runs its gate on every inbound message: enters when a `sales_conversation_state` row exists OR the inbound text matches the sales-intent regex; skips silently otherwise. A project with zero `services` rows can still scope, look up prices via RAG, and propose calendar dates; the catalog turn handles the empty-catalog case explicitly.

## Automated E2E (current repo)
Story-aligned E2E tests land in `tests/e2e/test_e2e_epic12_*.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("12")`, `@pytest.mark.story("12-0X")`). The earliest E2E belongs to 12.04 (price-unknown → HITL → moderation candidate → re-ask hits without escalation). 12.05b adds the KB-upload auto-analysis round-trip (sendable + non-sendable file fixtures). 12.07 covers the date-proposal handoff to Epic 11 with a mocked `compute_availability`. 12.08 covers the +1d nudge with a frozen clock (fires-in-window, cancel-on-reply, skip-if-stale). The full Данил dialog replay lands in 12.09. CI runs `pytest` (coverage) + `pytest -m e2e`. Story-level rows live in `_bmad-output/implementation-artifacts/e2e-coverage.md`. Scripted signoff: `scripts/epic12_signoff.sh`.

## Behavioral spec
The two extracted Telegram transcripts are the canonical behavioral spec for the answerer:
- [2026-04-28_telegram_chat_danil.md](/Users/aj/Downloads/2026-04-28_telegram_chat_danil.md) — routes → pricing per-vehicle → каньонинг concept → date proposal → next-day follow-up
- [2026-04-28_telegram_chat_darya.md](/Users/aj/Downloads/2026-04-28_telegram_chat_darya.md) — greeting (with referral) → intent scoping → media demo → equipment Q&A → tiered pricing menu

Story 12.09's E2E replays the Данил inbound messages end-to-end as the acceptance signal.
