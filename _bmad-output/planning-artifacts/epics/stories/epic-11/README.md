# Epic 11 Story Pack

Epic: Calendar Availability & Scheduling (read-only)

This story pack is implementation-ready and includes, per story:
- scope boundaries (in/out)
- implementation notes grounded in the architecture + project-context rules
- test requirements (unit / contract / integration)
- automated E2E + manual verification
- completion gates (100% coverage, ruff clean, secrets never logged)

**One PR per story.** Each story is a self-contained branch + PR mapping 1:1 to the BMAD `create-story → dev-story → code-review → PR` cycle.

Implementation order follows the dependency graph (11.01 is the foundation and blocks every later story; 11.05/11.06 are independent of the OAuth branch and can run in parallel):

```
11.01 calendar schema + settings + repos (settings, token[Fernet], state)   ← foundation, blocks all
  ├── 11.02 OAuth connect (api): consent URL + callback + code exchange + encrypted store + disconnect
  │     ├── 11.03 Telegram /connect_calendar + /disconnect_calendar command (bot_gateway)
  │     └── 11.04 token lifecycle + resilience (single-flight refresh, expiry/revocation→reconnect+notify+incident, freeBusy httpx client w/ timeout + 429)
  ├── 11.05 availability engine (pure compute_availability + service-rules model)   (parallel after 11.01)
  └── 11.06 service resolution FR-22 (RussianNormalizer lemma match → clarify-once → escalate)   (parallel after 11.01)
        11.07 CalendarAvailabilityAnswerer + pipeline wiring + E2E   (depends on 11.04, 11.05, 11.06)
```

## Story list
- `story-11-01-calendar-schema-and-settings.md`
- `story-11-02-oauth-connect-api.md`
- `story-11-03-telegram-connect-command.md`
- `story-11-04-token-lifecycle-and-resilience.md`
- `story-11-05-availability-engine.md`
- `story-11-06-service-resolution.md`
- `story-11-07-availability-answerer-and-wiring.md`

## Automated E2E (current repo)
Story-aligned E2E tests land in `tests/e2e/test_e2e_epic11_*.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("11")`). The earliest E2E belongs to 11.02 (OAuth connect round-trip with mocked Google + DM). The full availability round-trip (connect → freeBusy → answer) lands in 11.07. CI runs `pytest -m e2e` plus the standard `pytest` with coverage. Story-level rows live in `_bmad-output/implementation-artifacts/e2e-coverage.md`. Scripted signoff: `scripts/epic11_signoff.sh`.
