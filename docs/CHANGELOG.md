# Changelog

## v0.2.0 — 中文文献雷达 + 中英分区 + 桌面配置台（2026-08-09）

### 新增：中文文献数据源（NAPSTIC）
- 集成「国家学术搜索」(search.napstic.cn，中信所运营) 两个原生数据源：
  - `napstic_search`：按 `keywords.chinese` 关键词组逐词检索全部中文期刊（含网络首发 `online_date`），默认开启；
  - `napstic_journals`：11 本电力核心期刊逐期抓取（中国电机工程学报、电力系统自动化、电网技术等），默认关闭按需开启。
- 抓取脚本 `scripts/cn_napstic.py` 随仓库分发，默认 1.2–1.5s 低频节流。
- 中文文献与英文文献共用 筛选 → 去重 → 解读 → 邮件 全链路；去重键新增 `source_id` 回退。

### 新增：中英配额与双分区展示（宁缺毋滥）
- 按语言独立配额：`daily_target_en`（默认 10）、`daily_target_zh`（默认 5）；数量不足时少发、不凑数，两者皆空则不发送邮件。
- 附件 dashboard 一页两界面：顶部「语言分区」切换 🌐英文 / 🇨🇳中文 / 全部；digest Markdown 分「英文文献」「中文文献」两章。
- 默认关闭历史回填（`backfill_enabled: false`）。

### 新增：质量门与白名单
- `journal_filter.chinese_ei` 扩展至 21 本电力领域核心/主流期刊，中文文献全部过白名单；
- NAPSTIC 检索通道支持 `bypass_journal_whitelist` 开关（默认关闭，strict 过滤）。

### 新增：LLM 解读增强
- 端点与模型支持环境变量覆盖：`DEEPSEEK_BASE_URL`（兼容任意 OpenAI 兼容网关，如 opencodezen `https://opencode.ai/zen/v1/chat/completions`）、`DEEPSEEK_MODEL`（如 `deepseek-v4-flash-free`）；
- LLM 请求附加浏览器 User-Agent，可过部分网关的 UA 校验；
- 中文文献（标题含中文）不再调用 LLM 翻译——直接用原文中文摘要 + 本地规则解读，节省配额；
- 失败自动回退本地规则解读，不阻塞推送。

### 新增：桌面配置台（exe）
- `scripts/radar_app.py` + `scripts/build_exe.ps1`：PyInstaller onedir 打包，内嵌 WebView2 窗口（无需浏览器）；
- 用户数据（profiles/、凭据、输出）保存在 exe 同级目录，与网页版/命令行三种方式共用同一套配置；
- 构建脚本自动备份/恢复 `profiles/`，重建不影响方案数据。

### 增强：本地可视化配置台
- 运行设置拆分为「英文每日篇数 / 中文每日篇数 / 启用历史回填」；
- 新增「停止任务」按钮：一键停用当前方案全部 Windows 计划任务；
- 「本机接口与账号」可编辑 DeepSeek 端点/模型（显示实际生效值，凭据优先）；
- 修复按键绑定失效问题（按钮无响应）。

### 安全
- 所有 API Key / SMTP 授权码 / 收件人仅存于本机文件（gitignored），仓库与发布包均不含任何个人凭据。