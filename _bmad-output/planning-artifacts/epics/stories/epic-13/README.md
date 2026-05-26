# Epic 13 Story Pack

Epic: Unified Project Services Catalog

This story pack is implementation-ready and includes, per story:
- scope boundaries (in/out)
- implementation notes grounded in the architecture + project-context rules
- test requirements (unit / contract / integration)
- automated E2E + manual verification
- completion gates (100% coverage on new modules, ruff clean, secrets never logged — operator-published service content is non-secret and IS logged)

**One combined PR for the Epic 13 code, story-by-story commits inside.** Each story is a self-contained branch checkpoint mapping 1:1 to the BMAD `create-story → dev-story → code-review` cycle and carries its own 100% coverage gate; the six commits ship together in a single PR (per the approved Epic 13 plan).

Implementation order follows the dependency graph. 13.01 is the foundation and blocks every later story; 13.06 depends ONLY on 13.01 (catalog answerer cutover is independent of the NL branch and can run in parallel with 13.02→13.05):

```
13.01 schema/repo + migration + russian_calendar_terms.json + services_nl_op_sessions   ← foundation, blocks all
  ├── 13.02 canonical api + alias delegation (POST/GET/DELETE /api/projects/{id}/services + old-endpoint aliases)
  │     ├── 13.03 /service slash command + /calendar_service alias (start-of-message anchored; migration-hint DM)
  │     └── 13.04 services_nl_ops api (parse_service_intent + state machine + 4 endpoints; single-pending; confirm-verifies-owner)
  │           └── 13.05 services_nl_dialog bot (plain-text preview + ownership check + operator-gating)
  └── 13.06 catalog answerer merge-with-dedup + services_render + grounding_system rule   (parallel after 13.01)
```

## Story list
- `story-13-01-project-services-schema-and-repo.md`
- `story-13-02-canonical-services-api.md`
- `story-13-03-service-slash-command.md`
- `story-13-04-services-nl-ops-api.md`
- `story-13-05-services-nl-dialog-bot.md`
- `story-13-06-catalog-answerer-merge-with-dedup.md`

## Automated E2E (current repo)
Story-aligned E2E tests land in `tests/e2e/test_e2e_epic13_*.py` (`@pytest.mark.e2e`, `@pytest.mark.epic("13")`, `@pytest.mark.story("13-NN")`). The earliest end-to-end coverage belongs to 13.02 (canonical-api round-trip + alias delegation). The slash-command full round-trip lands in 13.03; the NL-dialog propose→confirm→upsert round-trip in 13.05; the catalog-answer label-leak + dedup round-trip in 13.06. CI runs `pytest` with coverage plus `pytest -m e2e`. Story-level rows live in `_bmad-output/implementation-artifacts/e2e-coverage.md`. Scripted signoff: `scripts/epic13_signoff.sh`.
