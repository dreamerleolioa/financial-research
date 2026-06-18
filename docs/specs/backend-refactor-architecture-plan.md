# Backend Refactor Architecture Plan

> Date: 2026-06-18
> Status: Phase 6 completed
> Scope: Backend architecture refactor plan for the existing FastAPI monolith.
> Runtime constraint: keep one FastAPI app, one SQLAlchemy/PostgreSQL database, one CI path, and the current Python 3.11 + uv stack.
> Relationship: this document is a temporary refactor decision and execution plan. It is not part of the canonical specs index. After the refactor is complete, delete this plan and update current system facts in `ai-stock-sentinel-architecture-spec.md`; API contracts remain in `backend-api-technical-spec.md`.

## 1. Decision

Adopt **Clean Architecture / Hexagonal Architecture + DDD-lite + TDD guardrails**.

This means:

- Keep the project as a modular FastAPI monolith.
- Use Clean Architecture to separate HTTP concerns, application use cases, domain rules, repositories, and adapters.
- Use Hexagonal Architecture to isolate external systems such as SQLAlchemy, LangGraph, yfinance, FinMind, RSS, TDCC/TWSE/TPEX, and LLM providers behind explicit ports/adapters where the boundary has real value.
- Use DDD-lite to name and protect the real product language without introducing heavy tactical DDD ceremony.
- Use TDD and characterization tests as a safety net before moving behavior out of large router/API files.

The target is better maintainability and safer feature work, not a new platform.

## 2. Why this fits this project

This backend is no longer a simple stock-analysis demo. It has multiple product surfaces that share data, evidence, and investment-discipline semantics:

| Product surface | Current entry points | Core domain pressure |
| --- | --- | --- |
| New-position analysis | `POST /analyze` | LangGraph orchestration, cache isolation, raw-data fallback, LLM boundary |
| Position diagnosis | `POST /analyze/position` | Existing-position risk language, deterministic action/risk fields |
| Portfolio lifecycle | `/portfolio/*` | Position events, lifecycle plans, add/close flows, trade/lifecycle reviews, risk summary |
| Daily Radar | `/internal/daily-radar/*`, `GET /daily-radar/*` | Deterministic scoring, provider freshness, shared background context, forward validation |
| Watchlist | `/watchlist/*` | Observation list, quick lookup, reorder semantics |

The current module layout already points toward bounded contexts:

- `daily_radar/`
- `portfolio/`
- `watchlist/`
- `analysis/`
- `graph/`
- `data_sources/`
- `db/`

The problem is not that the project lacks all architecture. The problem is that some files still mix too many responsibilities:

- `api.py` acts as FastAPI app setup, request schema, cache service, response assembler, graph invoker, raw-data endpoint, history endpoint, and shared-context attachment point.
- `portfolio/router.py` handles HTTP routing, request models, DB queries, serialization, lifecycle event writes, fees, ownership checks, and review orchestration.
- `daily_radar/router.py` handles internal workflow orchestration, public response serialization, request models, provider setup, and helper transformations.

At the same time, the project already has good shapes worth preserving:

- `daily_radar/service.py` is close to an application service: it orchestrates prefilter, scoring, background contexts, cooldown, explanations, and persistence.
- `portfolio/risk_summary.py` is close to a domain calculation module: it accepts inputs, computes exposure/risk states, and returns deterministic output.
- `graph/builder.py` isolates LangGraph topology from FastAPI routing.

The best architecture direction is therefore incremental modularization, not a rewrite.

## 3. What each architecture term means here

### 3.1 Clean Architecture

Clean Architecture means dependencies should point inward:

```text
FastAPI router / Pydantic schemas
  -> application use cases
    -> domain policies / calculators / rules
    -> repository and provider ports
      -> SQLAlchemy / LangGraph / yfinance / FinMind / RSS / LLM adapters
```

In this project, that translates to:

- Routers should handle HTTP concerns: auth, request parsing, response status codes, dependency injection, and response models.
- Application services should coordinate a user action: add a position, close a position, run analysis, run Daily Radar, create lifecycle review.
- Domain modules should hold deterministic rules: fees, risk summary, lifecycle evaluation, scoring, prefilter, cache eligibility.
- Repositories should hold query/write details where multiple use cases need the same persistence behavior.
- Adapters should isolate external systems only where the boundary protects tests or reduces coupling.

Clean Architecture is useful here because it gives a practical answer to "where should this code live?" without requiring a new service or framework.

### 3.2 Hexagonal Architecture

Hexagonal Architecture is about ports and adapters. A use case should depend on a capability, not directly on an external tool, when that tool is unstable, slow, expensive, or hard to test.

Good adapter candidates in this backend:

- Market data providers: yfinance, FinMind, TWSE, TPEX, TDCC.
- LLM providers: Anthropic/OpenAI through LangChain.
- LangGraph compiled graph invocation.
- SQLAlchemy persistence for complex use cases.
- Internal workflow triggers and shared-background-context reads.

Not every dependency needs a formal port. For a personal monolith, over-abstracting every table or helper will slow development. Use ports where they solve a concrete problem:

- Offline tests need provider injection.
- Multiple providers implement the same capability.
- Provider behavior has quota, freshness, token, or TLS concerns.
- A use case should be testable without network or database setup.

This is why the existing FinMind/token/cache seams and Daily Radar provider ownership should be preserved instead of flattened into generic helpers.

### 3.3 DDD-lite

DDD-lite means using the useful parts of Domain-Driven Design without imposing full tactical DDD everywhere.

Use:

- Bounded contexts.
- Ubiquitous language.
- Domain services for deterministic rules.
- Clear aggregates only where consistency boundaries are real.
- Explicit invariants around lifecycle, cache, and evidence semantics.

Avoid for now:

- Rewriting all SQLAlchemy models as rich domain entities.
- Creating repositories for every table.
- Introducing factories/specifications/value objects everywhere.
- Forcing aggregate roots where the current transaction boundary is simple.

The project already has strong domain language:

- `PositionLifecyclePlan`
- `PositionEvent`
- `TradeReview`
- `PositionLifecycleReview`
- `DailyRadarRun`
- `DailyRadarCandidate`
- `SharedBackgroundContext`
- `StockAnalysisCache`
- `StockRawData`
- `EntryRecordContext`

DDD-lite should protect that language and make boundaries clearer. It should not bury the project under pattern names.

### 3.4 TDD guardrails

TDD is not the architecture. It is the refactor safety system.

For this project, use tests in three layers:

| Test type | Purpose | Examples |
| --- | --- | --- |
| Characterization tests | Freeze current behavior before moving code | Existing `/portfolio/*` response behavior, `/analyze` cache hits, Daily Radar public response |
| Domain tests | Verify pure deterministic rules | risk summary math, fees, scoring, prefilter, lifecycle labels |
| Contract/router tests | Verify HTTP behavior and API compatibility | status codes, validation errors, response fields, auth ownership |

This matters because many important behaviors are semantic, not just structural:

- `/analyze` and `/analyze/position` caches must stay isolated by `analysis_type`.
- `shared_background_contexts` must remain evidence/cache only, not ranking/action/verdict override.
- Daily Radar scoring must remain deterministic and not become LLM-selected.
- Portfolio risk cannot silently fill missing price/stop/quantity with zero.
- Public Daily Radar reads should not start live metadata lookups as a side effect.

Tests should pin those behaviors before code moves.

## 4. Target package shape

This is a target direction, not a requirement to create every file at once.

```text
backend/src/ai_stock_sentinel/
  api.py                         # app setup, root routes, include routers
  analysis/
    router.py                    # future split for /analyze endpoints
    schemas.py
    application/
      analyze_stock.py
      analyze_position.py
      analysis_cache.py
      response_builder.py
    domain/
      risk_language.py
      technical_indicators.py
    adapters/
      graph_runner.py
  portfolio/
    router.py
    schemas.py
    application/
      add_position.py
      update_position.py
      add_entry.py
      close_position.py
      get_risk_summary.py
      create_trade_review.py
      create_lifecycle_review.py
    domain/
      fees.py
      risk_summary.py
      lifecycle_policy.py
    repository.py
  daily_radar/
    router.py
    schemas.py
    service.py
    repository.py
    scoring.py
    prefilter.py
    background_context.py
  data_sources/
    ...
  db/
    models.py
    session.py
```

Expected dependency direction:

```text
router -> application -> domain
router -> application -> repository/adapter interfaces
repository/adapter implementation -> db/data_sources/external libraries
domain -> standard library and local domain types only
```

Avoid these directions:

- Domain modules importing `fastapi`.
- Domain modules opening DB sessions.
- Domain modules calling yfinance, FinMind, RSS, TDCC/TWSE/TPEX, or LLM providers directly.
- Routers accumulating new scoring, lifecycle, risk, or cache rules.
- Data-source adapters importing router schemas.

## 5. Bounded context decisions

### 5.1 Analysis context

Owns:

- `/analyze`
- `/analyze/position`
- LangGraph invocation
- L1/L2 cache behavior
- response assembly
- technical indicator extraction
- shared context attachment for analysis consumers

Boundary rules:

- LLM may explain and summarize but must not compute or override deterministic technical/risk fields.
- General analysis and position analysis must remain cache-isolated.
- `skip_ai=true` remains a deterministic quick lookup path.

### 5.2 Portfolio context

Owns:

- active and closed positions
- add-entry and close flows
- position event ledger
- lifecycle plan and reviews
- trade review
- portfolio risk summary

Boundary rules:

- `position_group_id` remains the lifecycle identity.
- Event ledger writes must stay replayable and auditable.
- Risk summary must report caveats for missing or stale inputs instead of silently manufacturing values.

### 5.3 Daily Radar context

Owns:

- internal run workflow
- universe selection
- raw row preparation
- prefilter and scoring
- candidate persistence
- forward validation
- monthly rule governance
- public Daily Radar reads

Boundary rules:

- Daily Radar is not LLM stock picking.
- `observation_score` is a deterministic ranking/calibration trace, not a win-rate or trading command.
- Shared background context can add labels, caveats, and trace evidence but cannot overwrite bucket, score, ranking, action, verdict, or classification.
- Public reads should remain offline and read persisted results.

### 5.4 Data source context

Owns:

- yfinance access
- RSS news access
- FinMind client/token/quota/cache concerns
- institutional flow provider routing
- fundamental provider routing
- symbol metadata lookup

Boundary rules:

- Provider freshness, missing reason, token identity, and source trace are part of the data contract.
- Provider-specific failures should degrade into explicit missing/stale/caveat payloads when the product surface can continue.

## 6. Execution plan

Each phase must be independently mergeable. No phase should require the next phase to keep the app usable.

### Phase 1: Record backend architecture decision

Files:

- `docs/specs/backend-refactor-architecture-plan.md`

Actions:

- Record the chosen architecture approach.
- Record dependency direction and bounded context rules.
- Record what is explicitly out of scope.
- Keep the plan out of `docs/specs/README.md` because this file is temporary and will be deleted after execution.

Verification:

- Documentation review only.
- Confirm the plan does not contradict current architecture specs.

Rollback:

- Revert `docs/specs/backend-refactor-architecture-plan.md` only.

### Phase 2: Add characterization tests

Status: Completed on 2026-06-18.

Primary files:

- `backend/tests/test_portfolio_router.py`
- `backend/tests/test_portfolio_risk_summary.py`
- `backend/tests/test_api.py`
- `backend/tests/test_analysis_cache.py`
- `backend/tests/test_daily_radar_service.py`
- `backend/tests/test_daily_radar_api_contract.py`

Actions:

- Add tests for behavior that must not change during refactor.
- Prefer offline tests with dependency injection and fixtures.
- Cover happy paths, validation errors, stale/missing data, cache isolation, and shared-context boundaries.
- Added explicit `/analyze` characterization coverage for general-cache lookup and `skip_ai` recent raw-cache reuse.
- Confirmed existing Portfolio and Daily Radar tests already cover partial/full close events, risk-summary final raw-row selection, public Daily Radar reads, and shared-context evidence boundaries.

Required behavior coverage:

- Portfolio add/update/add-entry/close flows.
- Position event ledger writes.
- Portfolio risk summary caveats and total-at-risk math.
- `/analyze` cache hit and raw-data quick lookup behavior.
- `/analyze` vs `/analyze/position` cache isolation.
- Daily Radar public latest/date/symbol-history response shape.
- Daily Radar shared context remains evidence/trace only.

Verification:

```bash
cd backend
uv run pytest tests/test_portfolio_router.py tests/test_portfolio_risk_summary.py
uv run pytest tests/test_api.py tests/test_analysis_cache.py
uv run pytest tests/test_daily_radar_service.py tests/test_daily_radar_api_contract.py
uv run pytest tests/test_portfolio_history.py tests/test_daily_radar_api.py tests/test_graph_builder.py tests/test_graph_nodes.py
```

Rollback:

- Revert test additions only. No production behavior changes.

### Phase 3: Extract Portfolio application services

Status: Completed on 2026-06-18.

Primary files:

- `backend/src/ai_stock_sentinel/portfolio/router.py`
- `backend/src/ai_stock_sentinel/portfolio/schemas.py`
- `backend/src/ai_stock_sentinel/portfolio/repository.py`
- `backend/src/ai_stock_sentinel/portfolio/application/add_position.py`
- `backend/src/ai_stock_sentinel/portfolio/application/update_position.py`
- `backend/src/ai_stock_sentinel/portfolio/application/add_entry.py`
- `backend/src/ai_stock_sentinel/portfolio/application/close_position.py`
- `backend/src/ai_stock_sentinel/portfolio/application/get_risk_summary.py`

Actions:

- Move request/response models from router to `schemas.py`.
- Move repeated ownership and query/write logic to `repository.py` only where it is shared across use cases.
- Move add/update/add-entry/close orchestration into application services.
- Keep deterministic calculations in existing focused modules such as `fees.py` and `risk_summary.py`.
- Keep endpoint paths and response bodies unchanged.
- Added `portfolio/application/*` use cases for create, update, add-entry, close, and risk summary.
- Kept router-level response serialization and lifecycle/review endpoints in place for this phase.

Verification:

```bash
cd backend
uv run pytest tests/test_portfolio_router.py tests/test_portfolio_risk_summary.py tests/test_portfolio_history.py
```

Rollback:

- Revert Portfolio refactor files. No database migration should be part of this phase.

### Phase 4: Extract Analysis use cases

Status: Completed on 2026-06-18.

Primary files:

- `backend/src/ai_stock_sentinel/api.py`
- `backend/src/ai_stock_sentinel/analysis/schemas.py`
- `backend/src/ai_stock_sentinel/analysis/application/analyze_stock.py`
- `backend/src/ai_stock_sentinel/analysis/application/analyze_position.py`
- `backend/src/ai_stock_sentinel/analysis/application/analysis_cache.py`
- `backend/src/ai_stock_sentinel/analysis/application/response_builder.py`
- `backend/src/ai_stock_sentinel/analysis/adapters/graph_runner.py`

Actions:

- Move Pydantic request/response models out of `api.py`.
- Move cache read/write and raw-data selection into `analysis_cache.py`.
- Move response assembly into `response_builder.py`.
- Wrap LangGraph invocation behind a small graph runner adapter.
- Keep `graph/` topology and node behavior unchanged.
- Keep `/analyze`, `/analyze/position`, `/internal/fetch-raw-data`, and `/history/{symbol}` contracts unchanged.
- Added `analysis/schemas.py` for analysis request/response models.
- Added `analysis/application/analysis_cache.py` for analysis cache and raw-data cache helpers.
- Added `analysis/application/response_builder.py` for response assembly and indicator extraction.
- Added `analysis/application/analyze_stock.py` and `analysis/application/analyze_position.py` for Graph initial-state builders.
- Added `analysis/adapters/graph_runner.py` for graph singleton construction and invocation.
- Kept `api.py` compatibility wrapper names that existing tests monkeypatch directly.

Verification:

```bash
cd backend
uv run pytest tests/test_api.py tests/test_analysis_cache.py tests/test_graph_builder.py tests/test_graph_nodes.py
```

Rollback:

- Revert analysis refactor files. No database migration should be part of this phase.

### Phase 5: Slim Daily Radar router

Status: Completed on 2026-06-18.

Primary files:

- `backend/src/ai_stock_sentinel/daily_radar/router.py`
- `backend/src/ai_stock_sentinel/daily_radar/schemas.py`
- `backend/src/ai_stock_sentinel/daily_radar/presenter.py`
- `backend/src/ai_stock_sentinel/daily_radar/constants.py`
- `backend/src/ai_stock_sentinel/daily_radar/service.py`
- `backend/src/ai_stock_sentinel/daily_radar/repository.py`

Actions:

- Move request/response models to `schemas.py` if not already separated enough.
- Move public response serialization helpers out of the router only where it reduces router coupling.
- Keep scoring, prefilter, background context, provider routing, and repository semantics unchanged unless tests force a small correction.
- Do not change provider ownership or shared-context ranking boundaries in this phase.
- Moved internal Daily Radar request/response models from `router.py` to `schemas.py`.
- Added `presenter.py` for public run, candidate, symbol-history, and internal run-trigger response serialization.
- Moved the background-context type tuple to `constants.py`; `repository.py` keeps the existing `BACKGROUND_CONTEXT_TYPES` alias for compatibility.
- Kept Daily Radar run orchestration and institutional universe payload shaping in `router.py` for this phase.

Verification:

```bash
cd backend
uv run pytest tests/test_daily_radar_service.py tests/test_daily_radar_api.py tests/test_daily_radar_api_contract.py
uv run pytest tests/test_daily_radar_background_context.py tests/test_daily_radar_rule_governance.py
```

Rollback:

- Revert Daily Radar router/schema refactor files. No database migration should be part of this phase.

### Phase 6: Add import boundary guard

Status: Completed on 2026-06-18.

Primary files:

- `backend/tests/test_backend_architecture_boundaries.py`

Actions:

- Add a lightweight test that prevents the most dangerous dependency inversions.
- Check that domain modules do not import `fastapi`.
- Check that domain calculation modules do not import SQLAlchemy sessions or external providers.
- Check that routers do not become the only home for new deterministic business rules where an application/domain module exists.
- Added a low-noise AST-based boundary test for pure calculation modules in Analysis, Daily Radar, and Portfolio.
- Guarded refactored HTTP boundaries (`api.py`, Daily Radar router, Portfolio routers) from reintroducing Pydantic schema classes.
- Guarded Daily Radar router from reabsorbing public response presenter helpers that now live in `daily_radar/presenter.py`.
- Intentionally did not include Auth and Watchlist routers yet because those areas have not gone through this refactor.

Verification:

```bash
cd backend
uv run pytest tests/test_backend_architecture_boundaries.py
```

Rollback:

- Revert the boundary test if it is too noisy. Keep the documented direction.

## 7. Test strategy

Run the full backend suite after any phase that moves production code:

```bash
cd backend
uv run pytest
```

For narrow phase checks:

```bash
cd backend
uv run pytest tests/test_portfolio_router.py tests/test_portfolio_risk_summary.py
uv run pytest tests/test_api.py tests/test_analysis_cache.py
uv run pytest tests/test_daily_radar_service.py tests/test_daily_radar_api_contract.py
```

Testing priorities:

1. Preserve API contracts.
2. Preserve deterministic financial/risk/scoring behavior.
3. Preserve cache isolation and freshness semantics.
4. Preserve provider degradation behavior.
5. Preserve public read behavior for Daily Radar.

## 8. Out of scope

These are explicitly not part of this refactor plan:

- Splitting the backend into microservices.
- Adding a new job queue, worker runtime, or separate scheduler service.
- Replacing FastAPI, SQLAlchemy, Alembic, LangGraph, or uv.
- Rewriting SQLAlchemy models as rich domain entities.
- Introducing CQRS, event sourcing, or a separate read model.
- Changing public API response fields as part of the refactor.
- Changing database schema unless a future feature independently requires it.
- Changing Daily Radar scoring strategy, provider ownership, or shared-background-context semantics.

## 9. Risk and mitigation

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| API shape drift | Frontend depends on current response fields | Characterization and router contract tests before extraction |
| Cache behavior drift | `/analyze` and `/analyze/position` must not pollute each other | Explicit cache-isolation tests |
| Daily Radar semantics drift | Scoring and shared context are product-critical | Do not change scoring/provider logic in router-slimming phase |
| Over-abstraction | Personal project can become harder to work on | Add repositories/ports only where they reduce real coupling |
| Hidden DB transaction changes | Portfolio event ledger and lifecycle writes must stay auditable | Keep transaction scope visible in use cases and test event writes |

## 10. Success criteria

The refactor is successful when:

- Routers are mostly HTTP boundary code.
- Application services describe user actions in project language.
- Deterministic rules live in testable modules without FastAPI dependencies.
- External providers are injectable where tests or provider ownership require it.
- Existing API contracts and workflows remain unchanged.
- Full backend tests pass.
- Future feature work can answer "which module owns this behavior?" without rereading a thousand-line router.
