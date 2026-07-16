# Frontend Interface Refresh Plan

> Temporary execution plan.
>
> This file is not a long-term architecture source of truth. Delete it after the refresh is fully implemented and reviewed.
>
> After implementation, sync durable frontend structure, shared component, responsive, and presentation-boundary decisions into `docs/specs/frontend-architecture-spec.md`. Update `README.md` only if the public workflow or setup changes.

## Goal

Refresh the authenticated React application into a distinctive, compact personal financial research workspace that is easier to scan, works correctly at mobile widths, and preserves all existing product and data-contract boundaries.

The visual direction is a calm research terminal: ink-green neutrals, warm signal colors, aligned financial numbers, restrained motion, and fewer generic cards. The application should feel designed around Taiwan stock research rather than around a reusable admin template.

## Non-Goals

- Do not change backend endpoints, response contracts, scoring, ranking, or deterministic analysis behavior.
- Do not add a charting library or a heavy animation dependency in this refresh.
- Do not change copy-to-AI payload shape or remove the existing copy workflow.
- Do not expose internal scoring buckets, scoring weights, backend rule traces, or raw technical caveats in user-facing UI.
- Do not turn observation language into buy, sell, add, reduce, or exit recommendations.
- Do not redesign every modal before the core shell and high-density pages prove the direction.
- Do not perform a one-shot rewrite of the large route components.

## Evidence Reviewed

The review covered the signed-in production application in dark mode at desktop width, light mode at desktop width, and 375px mobile width.

Reviewed routes:

- `/analyze`
- `/watchlist`
- `/portfolio`
- `/portfolio/closed`
- `/daily-radar`

Reviewed implementation seams:

- `frontend/src/App.tsx`
- `frontend/src/index.css`
- `frontend/src/pages/AnalyzePage.tsx`
- `frontend/src/pages/WatchlistPage.tsx`
- `frontend/src/pages/PortfolioPage.tsx`
- `frontend/src/pages/ClosedPortfolioPage.tsx`
- `frontend/src/pages/DailyRadarPage.tsx`
- `frontend/src/components/TechnicalIndicatorsPanel.tsx`

## Current Baseline and Problems

### Cross-Application

1. The global title, user controls, and five navigation buttons share one horizontal header without a mobile layout. At 375px, the title and subtitle collapse into vertical text, the user panel clips, and the page creates horizontal overflow.
2. Navigation uses five equal pill-like buttons. Active state is visible, but route grouping and hierarchy are weak. `已結案持股` should be a child view of portfolio rather than a peer of every top-level workflow.
3. Most content uses the same `rounded-xl border bg-card shadow-sm` treatment. Search forms, KPI summaries, empty states, data rows, and warnings therefore appear to have equal importance.
4. Indigo is used as the default action and selection color across unrelated actions. The visual identity reads as a generic Tailwind dashboard.
5. Numeric information does not consistently use tabular figures or shared alignment rules.
6. The product title in `App.tsx` and route titles can produce multiple page-level headings without a clear document hierarchy.
7. The app has no skip-to-content link and no stable responsive app shell.

### Analyze

1. Before analysis, four empty report cards and a separate empty news card occupy most of the page.
2. `查詢個股資訊` and `開始 AI 分析` are adjacent but do not visually explain their different cost and depth.
3. The intended flow is not visually explicit: enter symbol, choose quick or full analysis, interpret risk discipline, inspect supporting dimensions, then copy or act.
4. Result sections use equal cards even though technical and risk-discipline information are more operationally important than empty supporting dimensions.

### Watchlist

1. Every row permanently displays a large note textarea, creating excessive empty space for short observation notes.
2. Reorder, quick lookup, save, and remove controls compete for attention.
3. The page is structurally closer to an editable table, but it is rendered as a stack of large card rows.
4. Quick lookup content can expand into an already tall row, increasing scanning cost.

### Portfolio

1. The risk summary occupies a large surface but contains only two visible numbers until expanded.
2. Six positions produce a very long page because every item is a full card with repeated labels and actions.
3. The most important comparison fields are not aligned across holdings: symbol, current state, unrealized result, defense distance, data freshness, and next action.
4. Primary, secondary, and destructive actions are scattered across both ends of each card.
5. Data caveats are useful but visually compete with the actual position state.

### Closed Portfolio

1. The empty state is a large blank container with minimal guidance.
2. Period controls and realized PnL are usable, but the hierarchy feels like another generic card instead of a review workspace.
3. The active and closed views do not currently feel like two states of one portfolio workflow.

### Daily Radar

1. The status summary is the strongest current page section, but it still uses four identical cards and repeats surface treatments.
2. Data freshness details are always expanded even when all sources match the scan date.
3. Raw source identifiers such as `technical_profile` and `background_context` leak into otherwise Chinese presentation labels.
4. Twenty candidates are rendered as repeated multi-line rows with the same explanation and two visible actions each, making the page unnecessarily long.
5. Candidate status, bucket, risk label, explanation, complete analysis, and watchlist action have no strong column alignment.
6. Mobile inherits the global shell overflow before the page content begins.

## Locked Design Direction

### Visual Thesis

Calm financial research terminal with ink-green surfaces, warm off-white text, restrained signal colors, compact data rows, and an editorial hierarchy. Visual character comes from alignment, density, and color roles rather than gradients, glass effects, or decorative illustrations.

### Content Thesis

- Orient: show the current workflow, latest data status, and the one primary action.
- Compare: align symbols, dates, numbers, and state labels so rows can be scanned vertically.
- Inspect: move supporting evidence into inline expansion or a detail drawer.
- Act: keep one obvious primary action and group secondary or destructive actions quietly.

### Interaction Thesis

- Data refresh uses a short signal highlight that fades into the normal surface.
- Buttons use a subtle `scale(0.96)` press state and explicit transform transitions.
- Inline expansion and detail drawers use short opacity and translate transitions with reduced-motion support.

## Design System

### 1. Theme and Atmosphere

Dark mode remains the primary research environment. Light mode uses warm ivory rather than pure gray-white so both themes share the same character. Decorative backgrounds remain off.

### 2. Color Palette and Roles

Use OKLCH tokens in `frontend/src/index.css`. Final values may be optically adjusted during browser verification, but roles must remain stable.

| Token                    | Dark                           | Light                    | Role                               |
| ------------------------ | ------------------------------ | ------------------------ | ---------------------------------- |
| `--color-canvas`         | `oklch(0.16 0.014 165)`        | `oklch(0.975 0.008 95)`  | Page canvas                        |
| `--color-shell`          | `oklch(0.19 0.016 165)`        | `oklch(0.945 0.012 155)` | Sidebar and app chrome             |
| `--color-surface`        | `oklch(0.22 0.017 165)`        | `oklch(0.995 0.004 95)`  | Main content surface               |
| `--color-surface-raised` | `oklch(0.255 0.018 165)`       | `oklch(0.985 0.006 95)`  | Menus, drawers, raised panels      |
| `--color-border`         | `oklch(0.38 0.018 165 / 0.55)` | `oklch(0.84 0.014 155)`  | Dividers and field boundaries      |
| `--color-text-primary`   | `oklch(0.94 0.010 95)`         | `oklch(0.24 0.020 165)`  | Main text                          |
| `--color-text-muted`     | `oklch(0.72 0.016 165)`        | `oklch(0.50 0.018 165)`  | Supporting text                    |
| `--color-accent`         | `oklch(0.70 0.115 155)`        | `oklch(0.54 0.125 155)`  | Selection and primary action       |
| `--color-signal`         | `oklch(0.78 0.135 75)`         | `oklch(0.64 0.140 70)`   | Attention and fresh-data highlight |
| `--color-positive`       | `oklch(0.73 0.120 150)`        | `oklch(0.52 0.125 150)`  | Positive financial state           |
| `--color-negative`       | `oklch(0.69 0.155 25)`         | `oklch(0.57 0.165 25)`   | Negative or destructive state      |

Indigo should be removed from the shared interaction language. Purple or cyan gradients are not allowed.

### 3. Typography

- Use the system Latin stack followed by `PingFang TC` and `Noto Sans TC` for coherent mixed-script rendering. Do not add a remote display font in Phase 1.
- Apply antialiasing at the root.
- Use tabular figures for prices, percentages, dates, counts, and numeric table columns.
- Use `text-wrap: balance` on short page titles and `text-wrap: pretty` on explanatory copy.
- Do not apply negative letter spacing to Chinese runs.
- Use three content levels: page title, section heading, row label. Avoid multiple competing large titles.

### 4. Components

#### Navigation

- Desktop: 224px sticky sidebar with grouped workflows and a quieter user area at the bottom.
- Mobile: compact top app bar plus four-item bottom navigation: `分析`, `關注`, `持股`, `雷達`.
- Keep `/portfolio/closed`, but expose it as a portfolio sub-view using `持有中` and `已結案` tabs.
- Active state uses a small filled marker and text weight, not a full bright pill for every item.

#### Buttons

- Primary: filled accent, 10px radius, minimum 40px target.
- Secondary: quiet surface with one-pixel divider or text-only treatment.
- Destructive: text or outline by default, filled only in the confirmation step.
- Icon-only buttons require `aria-label` and a 40px hit target.
- All buttons use explicit transform and opacity transitions, never `transition: all`.

#### Inputs

- Use a 10px radius and shared 40px control height.
- Persistent textareas are reserved for genuinely long-form input.
- Short notes display as text and expand into editing on demand.
- Validation and helper copy remain adjacent to the field.

#### Data Rows

- Prefer aligned rows over cards for repeated holdings, watchlist items, and radar candidates.
- Desktop uses grid columns with left-aligned labels and right-aligned numbers.
- Mobile uses compact stacked rows with the status and primary value in the first viewport.
- Repeated explanations move to a details expansion or drawer.

#### Status

- Status badges use semantic background and text colors, not border-heavy pills.
- Badge radius is pill-only. Buttons and panels do not use pill radius.
- Freshness, confidence, risk, and action are separate visual roles.

### 5. Layout

- CSS strategy: Tailwind utilities plus shared Tailwind v4 tokens in `index.css`. Do not add CSS Modules or CSS-in-JS.
- Spacing scale: 4, 8, 12, 16, 24, 32.
- Radius scale: 6px, 10px, 14px, pill.
- Desktop workspace max width: 1440px after the sidebar.
- Dense rows use generous horizontal spacing and restrained vertical padding.
- Only major workflow summaries and raised overlays receive panel treatment.

### 6. Depth

- Dark mode uses background lightness steps as the main depth cue.
- Light mode uses a visible canvas-to-surface step plus a small layered shadow only for raised surfaces.
- Borders remain one pixel and are used for separation, not decoration.
- Glass blur is not part of the default visual language.

### 7. Project Guardrails

- Do preserve copy-to-AI controls and neutral raw/context payloads.
- Do keep user-facing state labels in readable Chinese.
- Do align all financial numbers with tabular figures.
- Do make risk and data freshness visible without letting caveats dominate the row.
- Do use details drawers or inline expansion for secondary evidence.
- Do not expose internal score buckets or raw backend caveat arrays.
- Do not give every section a rounded card and shadow.
- Do not use accent color on every action.
- Do not make mobile a compressed desktop layout.
- Do not remove safety, confirmation, or review steps from destructive portfolio actions.

### 8. Responsive Behavior

- `0-639px`: compact top bar, four-item bottom navigation, single-column content, 16px page padding.
- `640-1023px`: compact sidebar or rail, two-column summaries where useful, 20px content padding.
- `1024px+`: full 224px sidebar and dense multi-column workspace.
- Minimum interactive target: 40px, preferably 44px for bottom navigation.
- No horizontal page scrollbar at 320px or 375px.
- Tables must transform into intentional mobile rows rather than forcing horizontal scrolling for core workflows.

### 9. Implementation Prompt Guide

Use these constraints when implementing follow-up components:

1. App shell: `canvas oklch(0.16 0.014 165)`, 224px sticky sidebar at 1024px+, 10px control radius, 16px content gap, 40px targets, active item uses `accent oklch(0.70 0.115 155)` marker and medium-weight text.
2. Holding row: 14px body, 12px metadata, tabular right-aligned PnL and defense values, 12px vertical padding, one-pixel divider, primary analysis action visible, secondary actions in a contextual menu.
3. Radar row: symbol and name left, repeat and bucket state center, risk and primary action right, 12px vertical padding, repeated explanation hidden until expanded.
4. Mobile top bar: 56px height, product mark left, theme and user menu right, no global subtitle, no horizontal overflow at 320px.
5. Refresh highlight: signal background at 14% opacity fading to transparent over 600ms, transform-free, disabled under `prefers-reduced-motion`.

## Execution Phases

### Phase 1: Foundations and App Shell

#### Scope

- Replace existing slate and indigo theme values with semantic OKLCH tokens.
- Build shared shell, sidebar, mobile top bar, and mobile bottom navigation.
- Group active and closed portfolio routes under one portfolio navigation family.
- Add skip-to-content and correct heading hierarchy.
- Add shared button, input, badge, panel, and data-row utility patterns without building a full component library.

#### Files

- `frontend/src/index.css`
- `frontend/src/App.tsx`
- `frontend/src/main.tsx`, only if route metadata or layout composition is needed
- New focused components under `frontend/src/components/app-shell/` or an equivalent small shared boundary

#### Definition of Done

- No horizontal overflow at 320px and 375px.
- Desktop, tablet, and mobile navigation clearly expose all existing routes.
- Dark and light themes share one semantic token set.
- Product title and route title create one correct page heading hierarchy.
- Existing authentication, routing, and theme persistence still work.

### Phase 2: Analyze and Watchlist Workflow

#### Scope

- Turn Analyze into an explicit quick-data versus full-analysis workflow.
- Replace pre-analysis report cards with one useful empty state.
- Make result hierarchy prioritize risk discipline and technical context.
- Convert Watchlist into compact editable rows.
- Edit notes on demand instead of rendering large textareas permanently.
- Preserve quick lookup and copy-to-AI behavior exactly.

#### Definition of Done

- Analyze first viewport clearly explains the next action.
- Empty state uses substantially less vertical space.
- Watchlist shows at least several symbols per desktop viewport without hiding essential actions.
- Quick lookup, batch lookup, note save, reorder, remove, and copy workflows remain intact.

### Phase 3: Portfolio Workspace

#### Scope

- Convert the risk summary into a compact KPI strip with an expandable detail section.
- Convert repeated holding cards into aligned desktop rows and intentional mobile cards.
- Show position identity, state, PnL, defense distance, freshness, and primary action in a stable order.
- Move secondary actions into a contextual action menu while keeping destructive confirmations.
- Make `持有中` and `已結案` feel like two states of one workflow.

#### Definition of Done

- Holdings can be compared vertically without reading each card line by line.
- Positive, negative, stale, missing, and elevated-risk states remain distinguishable in both themes.
- No portfolio calculation, mutation, lifecycle, or review behavior changes.
- Existing risk caveats stay visible but secondary to the position state.

### Phase 4: Daily Radar Density and Detail

#### Scope

- Keep the current top status concept but remove identical-card repetition.
- Collapse all-fresh source details behind a summary disclosure.
- Translate `technical_profile`, `background_context`, and other remaining raw source labels in the presentation layer.
- Make candidate filters sticky within the workspace.
- Convert candidates into compact comparable rows.
- Keep the existing details drawer and watchlist action, but reduce repeated always-visible explanation text.

#### Definition of Done

- Twenty candidates are meaningfully scannable without a multi-screen card stack.
- Candidate name, symbol, repeat state, bucket, risk, and actions align consistently.
- Raw backend identifiers are not shown when a readable Chinese label exists.
- Ranking and scoring semantics remain unchanged.

### Phase 5: Login, Empty States, and Final Polish

#### Scope

- Bring login and callback screens into the same visual system.
- Add useful empty states for closed portfolio, Analyze, Watchlist, and unavailable Radar data.
- Add refresh highlight, press feedback, focus-visible treatment, and reduced-motion handling.
- Review all drawers and modals for consistent headers, actions, and mobile geometry.

#### Definition of Done

- Login is recognizably part of the same product.
- Empty states explain what the user can do next without oversized blank cards.
- All primary actions, keyboard focus, and loading states are visible in both themes.

### Phase 6: Post-Acceptance E2E Quality Gate

Start this phase only after the user has completed the final visual acceptance of Phases 1 through 5. Lock the accepted user journeys before adding browser automation so the suite protects the approved interface instead of freezing an intermediate design.

#### Scope

- Add a frontend E2E runner and repository scripts using the smallest setup that supports authenticated local testing.
- Cover the shared app shell, route access, desktop sidebar, mobile bottom navigation, portfolio sub-view switching, and theme persistence.
- Add focused happy-path coverage for Analyze, Watchlist, active Portfolio, closed Portfolio, and Daily Radar.
- Protect copy-to-AI, confirmation, and destructive-action guardrails without asserting unstable visual implementation details.
- Add viewport checks for 1280px, 375px, and 320px, including a no-horizontal-overflow assertion on core routes.
- Document local and CI execution, test data boundaries, and authenticated-session setup.

#### Definition of Done

- The accepted cross-route workflows have stable browser-level regression coverage.
- Tests use durable roles, labels, and explicit test IDs only where semantic selectors are insufficient.
- The suite does not depend on a developer's personal browser session or production data.
- E2E can run locally and in CI with deterministic fixtures or an isolated test account.
- Build, lint, and E2E all pass before future frontend changes are considered complete.

## Verification Matrix

Run after every phase:

```bash
cd frontend
pnpm run build
pnpm run lint
```

Browser verification:

| State         | Width             | Routes                                        |
| ------------- | ----------------- | --------------------------------------------- |
| Dark desktop  | 1280px or wider   | All five current routes                       |
| Light desktop | 1280px or wider   | Analyze, Portfolio, Daily Radar               |
| Dark mobile   | 375px             | All five current routes                       |
| Narrow mobile | 320px             | App shell, Analyze actions, Portfolio actions |
| Long content  | Desktop and 375px | Portfolio and Daily Radar                     |
| Empty content | Desktop and 375px | Analyze and Closed Portfolio                  |

Required checks:

- No horizontal page overflow.
- No clipped Chinese labels or orphaned two-character final lines in key headings.
- All numeric columns use tabular figures and consistent alignment.
- Focus order reaches skip link, navigation, primary action, and content in logical order.
- `prefers-reduced-motion` removes nonessential refresh and transition effects.
- Copy-to-AI output remains unchanged by layout changes.
- Backend caveats and internal score buckets remain outside the user-facing surface unless already explicitly mapped to a user-relevant state.

## Recommended Execution Order

1. Phase 1, Foundations and App Shell.
2. Phase 2, Analyze and Watchlist.
3. Phase 3, Portfolio Workspace.
4. Phase 4, Daily Radar.
5. Phase 5, Login and final polish.
6. Final visual acceptance by the user.
7. Phase 6, post-acceptance E2E quality gate.

Each phase should be independently reviewable. Do not start the next phase until the current phase passes build, lint, and real browser verification in both a desktop and 375px viewport.

## Confirmed Decisions

1. Active and closed positions share a single `持股` navigation family while retaining both existing URLs.
2. Desktop navigation changes from horizontal tabs to a left sidebar. Mobile uses a compact top bar and four-item bottom navigation.
3. Phase 1 stops after the app shell and shared tokens for visual review before any route content is restructured.
4. E2E implementation starts after the final visual acceptance of the complete refresh.

## Documentation Sync After Implementation

| Canonical doc                              | Sync when                                                                                             |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `docs/specs/frontend-architecture-spec.md` | App shell, shared UI boundary, route grouping, responsive structure, or presentation mapping changes. |
| `README.md`                                | Only if the public workflow, route summary, or setup behavior changes.                                |
