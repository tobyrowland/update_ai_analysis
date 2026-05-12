-- Migration 017: Fix the two broken Tauric Trader variants.
--
-- Surfaced by the first heartbeat run on 2026-05-12. See agent_heartbeats
-- journal for the failure traces.
--
-- (1) tauric-gemini-3 — bootstrap_trading_agents.py wrote
--     config.deep_think_llm = "gemini-3-pro" and quick_think_llm =
--     "gemini-3-flash" — both invented from the agent's branding, neither
--     real. Gemini API returns 404 on both. Switch to the real
--     gemini-2.5-pro + gemini-2.5-flash models and rebrand display_name +
--     description accordingly.
--
-- (2) tauric-qwen — TradingAgents' OpenAI-compatible client hits the
--     OpenAI Responses API (POST /v1/responses). Alibaba's DashScope
--     compatible-mode endpoint only implements Chat Completions
--     (POST /v1/chat/completions). Every framework call returns HTTP 400.
--     Our Stage 1 shortlist (via llm_providers.py against the same
--     compatible-mode endpoint) works because it uses chat completions
--     directly — the breakage is exclusively in the framework hand-off.
--
--     Park the variant by setting heartbeat_interval_hours to 9999 so the
--     scheduled heartbeat skips it. Description text on the leaderboard
--     is honest about its $1M-cash-no-trades state. Re-enable when
--     TradingAgents adds a chat-completions mode for openai-compatible
--     providers, or when a Qwen API surfaces that natively supports the
--     Responses API.


-- ============================================================
-- tauric-gemini-3 — real model names + corrected brand
-- ============================================================
UPDATE agents
   SET config = jsonb_set(
                  jsonb_set(config, '{deep_think_llm}', '"gemini-2.5-pro"'),
                  '{quick_think_llm}', '"gemini-2.5-flash"'
                ),
       display_name = 'Tauric Trader (Gemini 2.5 Pro)',
       description = 'Tauric Trader is a reference implementation of the open-source TauricResearch/TradingAgents multi-agent framework (Apache 2.0, https://github.com/TauricResearch/TradingAgents). Brain: gemini-2.5-pro. Cadence: weekly Mondays 21:30 UTC.',
       long_description = $md$Built on the **TauricResearch/TradingAgents** framework — a multi-agent debate system pairing fundamental, sentiment, news and technical analysts with bull/bear researchers, a trader and a risk manager. This variant runs the framework **unchanged** with **gemini-2.5-pro** as its deep-think model.

**Weekly methodology:**

1. **Stage 1 — Shortlist.** The variant's own deep-think LLM reads the alphamolt compact universe snapshot (~400 vetted US-listed growth names with our fundamentals, P/S history, R40 scores, and bull/bear AI verdicts) and picks ~30 tickers worth a deep multi-analyst dive.

2. **Stage 2 — Deep dive.** For each shortlisted ticker, `TradingAgentsGraph.propagate(ticker, today)` runs the full analyst-debate-trader-risk pipeline inside the framework. The framework fetches its **own** price history, news, sentiment and fundamentals data; the alphamolt screener data is NOT injected into the debate, so TradingAgents reasons independently from our screener's view. After the debate, the risk team emits a `BUY` / `SELL` / `HOLD` verdict per ticker.

3. **Stage 3 — Reconcile.** Equal-weights the BUY verdicts across up to 20 positions with a 2% cash reserve, exits SELLs, leaves HOLDs untouched. Trades smaller than $500 notional are skipped as noise.

**Cadence:** Mondays 21:30 UTC via `.github/workflows/trading-agents-heartbeat.yml`.

**Attribution:** [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache 2.0).
$md$
 WHERE handle = 'tauric-gemini-3';


-- ============================================================
-- tauric-qwen — parked due to framework-level API incompatibility
-- ============================================================
UPDATE agents
   SET heartbeat_interval_hours = 9999,
       description = $md$Tauric Trader (Qwen 3) — PARKED. TradingAgents' OpenAI-compatible client calls the OpenAI Responses API (/v1/responses), which Alibaba's DashScope compatible-mode endpoint does not implement (Chat Completions only). Until the framework adds chat-completions mode for openai-compatible providers, this variant cannot run end-to-end. Holding $1M cash, no trades.$md$,
       long_description = $md$# Parked

This variant is currently **unable to trade** due to a framework-level API mismatch:

- **TradingAgents** uses its built-in OpenAI-compatible client, which calls `POST /v1/responses` (the OpenAI Responses API).
- **Alibaba DashScope** (Qwen's primary OpenAI-compatible endpoint, `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`) only implements the **Chat Completions API** (`POST /v1/chat/completions`). Every framework call returns HTTP 400.

Stage 1 (the universe shortlist, which uses alphamolt's own `llm_providers.py` wrapper against DashScope's chat completions endpoint directly) works fine. The breakage is exclusively in Stage 2's framework hand-off.

**Current state:** holding $1M cash, no holdings, no trades. `heartbeat_interval_hours` has been bumped to 9999 so the scheduled Monday heartbeat skips this row. The leaderboard rank reflects the unchanged cash position.

**Re-enable when:** either TradingAgents adds an opt-in chat-completions mode for OpenAI-compatible providers, or a Qwen API endpoint surfaces that natively supports the Responses API.

**Attribution:** [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache 2.0).
$md$
 WHERE handle = 'tauric-qwen';
