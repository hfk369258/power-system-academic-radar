---
name: power-system-literature-radar
description: Track, configure, run, and summarize academic-literature monitoring for power system configuration, planning, operation, optimal dispatch, OPF, unit commitment, economic dispatch, distribution network reconfiguration, integrated energy systems, microgrids, energy storage, demand response, renewable integration, and related Chinese/English keywords. Use when the user asks for a power-system paper radar, IEEE/CNKI source setup, literature alerts, periodic digests, manual export ingestion, or source/keyword tuning for this field.
---

# Power System Literature Radar

Use this skill to configure and run the bundled literature radar for power-system configuration and optimal-dispatch research.

## Safety Boundary

Never ask for or store IEEE, CNKI, Web of Science, Scopus, or institutional passwords. Prefer official APIs, personal API keys stored in environment variables, RSS feeds, or manual exports downloaded by the user from their own logged-in browser session.

For IEEE, prefer the IEEE Xplore API (`IEEE_XPLORE_API_KEY`) or manual RIS/BibTeX/CSV exports. For CNKI, prefer manual export files placed in a watched folder unless the user provides an authorized institutional API or an explicit permitted integration path.

Read `references/source-access.md` before adding login-backed sources or changing credential handling.

## Quick Workflow

1. Copy or edit `assets/power_system_radar_config.json` for a runnable no-dependency config. Use `assets/power_system_radar_config.yaml` when PyYAML is installed.
2. Put manual exports from IEEE/CNKI/Web of Science/Scopus in the configured `manual_exports.paths` folders.
3. Validate without network:

```powershell
python scripts/power_system_radar.py --config assets/power_system_radar_config.json --validate-config
```

4. Preview source queries without network:

```powershell
python scripts/power_system_radar.py --config assets/power_system_radar_config.json --dry-run
```

5. Run the radar:

```powershell
python scripts/power_system_radar.py --config assets/power_system_radar_config.json --run
```

The runner writes a Chinese Markdown digest, JSON records, and a state file so repeated runs can suppress already-seen items. The digest includes a Chinese reading layer for each paper: research problem, method route, likely innovation, application scenario, relevance to the user's topic, and caveats to verify in the full text.

## DeepSeek Interpretation

Set `DEEPSEEK_API_KEY` in `radar.env.ps1` to enable `llm_interpretation`. DeepSeek is used only after records are fetched, scored, and deduplicated; it improves the Chinese reading layer and does not replace source APIs such as Semantic Scholar.

The default config sends only the top 5 papers to DeepSeek each run to control cost. If the DeepSeek call fails, the radar falls back to the local rule-based interpretation and still writes/sends the digest.

## Journal and OA Filtering

Use `journal_filter` in `assets/power_system_radar_config.json` to restrict results to the configured IEEE Transactions, high-level Elsevier/SCI journals, and selected Chinese EI journals. Impact factor and JCR/CAS quartile data change by year and are not reliably exposed by OpenAlex/Crossref/Semantic Scholar, so the radar treats the configured list as the source of truth.

Use `output_policy.full_analysis_requires_oa = true` when the digest should distinguish OA/open-version records from non-OA records. Records with abstracts receive Chinese analysis; non-OA or unknown-access records are explicitly labeled as abstract-based analysis rather than full-text reading.

Records without abstracts are kept only as title/keyword pre-screening entries. They are not sent to DeepSeek for full interpretation and should be manually opened before being treated as useful literature.

Use `abstract_enrichment` to reduce missing abstracts. The radar first tries DOI-based OpenAlex lookup, then title-based OpenAlex lookup with a similarity check, and optionally uses Semantic Scholar when `SEMANTIC_SCHOLAR_API_KEY` is available. Enriched abstracts are marked with `abstract_source` in the JSON and Markdown digest.

The `llm_power` keyword group gives extra priority to LLM/foundation-model/RAG/agent research when it is tied to power-system dispatch, OPF, configuration, distribution networks, or source-grid-load-storage topics.

## Email and WeChat Delivery

Use email for full digest delivery. Enable `notifications.email.enabled`, then provide SMTP settings through environment variables, not in the config file:

```powershell
$env:RADAR_SMTP_HOST = "smtp.example.com"
$env:RADAR_SMTP_PORT = "465"
$env:RADAR_SMTP_USER = "your_email@example.com"
$env:RADAR_SMTP_PASSWORD = "your_smtp_app_password"
$env:RADAR_EMAIL_FROM = "your_email@example.com"
$env:RADAR_EMAIL_TO = "target@example.com"
```

Use WeChat through a compliant webhook service, such as a WeCom group robot, ServerChan-style bridge, or PushPlus-style bridge. Enable `notifications.wechat.enabled` and set:

```powershell
$env:RADAR_WECHAT_WEBHOOK_URL = "https://..."
```

The WeChat message contains a compact top-paper digest because most webhook channels have message-length limits. The full Markdown digest is still written locally and can be sent by email.

## Windows Scheduling

Use `scripts/run_radar.ps1` for one-shot execution. It loads `radar.env.ps1` if present, creates manual-export folders, runs the Python radar, writes logs to `logs/`, and sends email/WeChat when enabled.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_radar.ps1 -EnableEmail -EnableWeChat
```

Use `scripts/setup_windows_task.ps1` to register a daily Windows Scheduled Task. The default time is 08:30 and can be changed with `-DailyTime HH:mm`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_windows_task.ps1 -DailyTime 08:30 -EnableEmail -EnableWeChat -Force
```

The scheduled task invokes `run_radar.ps1` and exits after each run; it does not keep PowerShell open.

## Keyword Tuning

Keep keywords in themed groups:

- Core power-system terms: power system, distribution network, active distribution network, smart grid, integrated energy system.
- Configuration terms: planning, expansion planning, network reconfiguration, topology optimization, capacity configuration, siting and sizing.
- Dispatch terms: optimal dispatch, economic dispatch, unit commitment, SCUC, SCED, OPF, security-constrained OPF, real-time dispatch.
- Flexibility terms: energy storage, demand response, virtual power plant, microgrid, distributed energy resources, renewable integration.
- Method terms: robust optimization, stochastic optimization, chance-constrained optimization, MILP, ADMM, MPC, reinforcement learning.

Include Chinese equivalents for CNKI/manual exports: 电力系统, 配电网, 主动配电网, 优化调度, 最优潮流, 机组组合, 经济调度, 网架重构, 源网荷储, 综合能源系统, 微电网, 储能配置.

Use exclusions to reduce noise from unrelated power electronics, semiconductor devices, wireless networks, battery chemistry-only papers, and generic machine-learning papers without grid-operation relevance.

## Source Strategy

Use public sources for routine monitoring:

- OpenAlex and Crossref for broad metadata discovery.
- arXiv for optimization/control/preprint signals.
- Semantic Scholar for open metadata, abstracts, and open-access PDF links when available.
- RSS feeds for journal table-of-contents alerts.
- Manual export folders for IEEE, CNKI, Web of Science, Scopus, Engineering Village, and publisher portals.
- IEEE Xplore API when the user has an API key.

For source-specific setup, update `sources[]` and `manual_exports` in the config. Do not hardcode secrets in config; use `env` fields such as `IEEE_XPLORE_API_KEY`.

If IEEE/CNKI API access is unavailable, keep `ieee_xplore.enabled` off and use OpenAlex, Crossref, arXiv, Semantic Scholar, RSS feeds, and manual export folders as the default pipeline.

Read `references/open-sources.md` when tuning open/preprint/RSS sources.
Read `references/email-setup.md` when configuring SMTP delivery.

## Review Standards

After running, inspect the generated digest for:

- false positives caused by broad optimization terms,
- duplicate titles from multiple sources,
- Chinese/English title variants of the same paper,
- missing DOI/year/venue fields,
- items hidden by overly strong exclusions.

When changing scoring or keyword groups, run `--dry-run` and then a small `--run --max-results 5` pass before relying on scheduled automation.
