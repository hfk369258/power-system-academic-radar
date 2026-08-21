# Changelog

## v0.3.4 — 误报修复与版本号对齐（2026-08-21）

### 修复
- LLM 可用性误报：本轮没有可解读的英文摘要文献（全部为中文文献，或英文文献均无摘要仅题名初筛）时，不再误报「⚠ LLM 中文解读本轮不可用（未知错误）」。原逻辑只看「成功数 == 0」就报警，未区分「没尝试」与「尝试后失败」；现改为只有真正发起过 LLM 调用且全部失败才显示该警告，部分失败仍提示「已用本地规则摘要补充」，行为不变；
- 插件版本号元数据 `.codex-plugin/plugin.json` 从遗留的 `0.2.0+codex.20260809` 对齐到当前版本，插件列表/控制台不再显示旧版本号。

### 测试
- pytest 80 项 + 5 subtests 全部通过。

## v0.3.3 — zcode 增量合并（2026-08-13）

合并 zcode 在 v0.3.2 基础上的增量优化,并修正其 3 处小缺陷、补齐我的晚到修复:

### 抓取正确性(来自 zcode)
- IEEE Xplore 结果补日期窗口过滤:接口只支持按年过滤,不再把过去一年的旧文在首次运行时全部推送;
- RSS 源补日期过滤(pubDate/updated 统一解析),无日期条目保守保留;
- 排除词(-99)对 manual/napstic 中文源同样生效,修复最低分豁免绕过排除词的漏洞;
- Crossref 日期年/月/日零填充,修复 "2026-8-1" 与 "2026-08-01" 字符串排序错位;
- `read_http_json` 对 5xx/URLError/连接错误做小退避重试;LLM 对 400/401/402/403/404 不重试(鉴权类重试无意义)。

### 省配额与体验(来自 zcode)
- LLM 解读结果本地缓存(`llm_cache.json`,2000→1500 修剪):basic/full 双方案与失败重跑不再重复消耗配额;
- LLM 可用性提示(402 余额不足/401 Key 无效等)展示在日报、邮件、dashboard 与企业微信;
- 通知语义升级:`maybe_notify` 返回 (delivered_any, all_ok),新增「⚠ 部分送达」历史状态;启用但未配置的渠道只告警,不再拖累整体判定;
- 发送记录面板:60s TTL 缓存 + `/api/outputs/file` 直接打开日报/仪表盘;
- 新增「本地维护」清理面板:可在控制台安全清理 `.trash`/`.env-backup`/`.private-backup-*`(路径白名单校验);
- 端口被占用时自动顺延(最多 20 个端口);计划任务错峰提醒(间隔 <10 分钟提示);`--since` 参数校验;
- `run_radar.ps1`:日志轮转(清理 30 天前运行日志与残留临时文件)+ 子进程 `PYTHONIOENCODING=utf-8`,防止西文 locale 下中文告警 UnicodeEncodeError 崩溃。

### 本版修复与补齐(合并方)
- 邮件卡片正确标注「DeepSeek 解读(缓存)」(原显示为「关键词初筛」);
- 前端「刷新记录」按钮传入 `refresh=1`,60s 缓存不再导致手动刷新无响应;
- LLM 缓存键增加规范化标题索引:同篇论文跨源换稳定键也能命中缓存;
- 去重升级为别名键集合(DOI/规范化标题/来源 ID),并把 zcode 的「排除词优先」与「未推送优先」合并进别名流程;
- 保留 v0.3.2 晚到修复:输出保留策略 `output_retention_days`、计划任务 `-Disable` 真停用与 `-Remove` 真删除语义;
- 修复 exe 控制台数据根目录:部署在仓库 dist 布局内时,exe 控制台直接读写仓库根目录的 profiles/凭据/日志,与每日计划任务完全一致,不再维护 dist 内副本;
- 测试 80 项全部通过。

## v0.3.2 — 可靠性加固与抓取提速（2026-08-13）

### 引擎（scripts/power_system_radar.py）
- 数据源并发抓取：OpenAlex/Crossref/arXiv/Semantic Scholar/IEEE/Scopus/RSS 以最多 4 路并行抓取，各源独立节流互不阻塞；NAPSTIC 两源保持串行低频访问；
- 源级故障全面隔离：任何单个数据源的意外异常（含配置值错误、连接重置、SSL 失败等）只告警跳过，不再拖垮整轮推送；
- 状态文件保护：损坏的状态文件自动备份为 `state.json.corrupt-*` 并告警，不再被静默清空（避免历史文献重复推送轰炸）；写入前增加进程内互斥；
- 运行锁加固：锁文件记录 pid 与 token，仅当持锁进程确实已死才接管 stale 锁；退出时只删除自己创建的锁，stale 阈值 2h → 6h；
- 通知逐渠道隔离：邮件/企微/Webhook 任一渠道异常只记录失败，不再导致整轮崩溃或误判；至少一个渠道送达即记入已推送状态，避免已送达渠道次日重复轰炸；
- LLM 重试改为指数退避 + 抖动，429 时遵循 `Retry-After`；错误日志对多风格 API Key 统一打码；
- 安全：RSS 源拒绝内网/环回/云元数据地址（防 SSRF）；arXiv 默认 HTTPS 并校验证书；digest 与企微 Markdown 对标题/期刊/作者等外部元数据转义；
- 修正 `backfill_enabled` 代码兜底默认值为 `false`（与模板、README 一致）；
- 跨源去重改为「别名键集合」：同一论文在不同源（DOI 键 / 规范化标题键 / 来源 ID 键）任一别名命中已推送状态或本轮记录即合并，不再“换键重生”造成重复推送；
- 新增可选输出保留策略 `profile.output_retention_days`（默认 0 = 永久保留）：超过天数的日报/仪表盘/记录文件自动清理，`history.jsonl` 与状态文件不受影响；
- 移除三处死代码（render_markdown / _chip_colors / group_items_by_primary_keyword）。

### 中文源（scripts/cn_napstic.py）
- 节流收口到 `http_get` 内部：每次请求前统一保证最小间隔，`--full` 详情补全不再以 0.6s 折半间隔请求；
- 重试改为指数退避 + 抖动；404/410/400/403 等永久性错误不再重试；
- 翻页终止不再假设每页固定 10 条（平台改版时不再静默漏抓后半期），页数上限可配置；
- 详情补全失败改为计数告警，不再静默丢弃；`article_id` 提取不再写死 `010` 前缀。

### 控制台（scripts/radar_config_ui.py + radar_control_panel.html）
- CSRF/DNS rebinding 加固：写接口要求同源 Origin 或 `Sec-Fetch-Site: same-origin`，拒绝跨站表单等无来源标记的写请求；
- 敏感凭据（API Key / SMTP 授权码）改为密码框 + 显示/隐藏切换；
- 删除方案/停止任务时计划任务 subprocess 移出配置锁，不再卡死整个控制台；
- 「恢复上次保存」改为原子写回；发送记录面板对半截 history 文件容错；
- 计划任务语义修正：`setup_windows_task.ps1` 的 `-Disable` 改为真正的「停用」（保留任务、禁用触发，与 UI「停止任务」文案一致），新增 `-Remove` 用于删除（方案删除与旧版混合任务迁移）；
- 端口被占用时输出中文诊断（exe 无窗口模式弹 MessageBox 并写入 `logs/console-ui.log`）；
- 前端错误提示精确化（后端中文报错直接透传，不再误报“无法连接”）。

### 测试
- 测试从 60 项增至 74 项：新增状态损坏备份、源异常隔离、部分渠道送达、stale 锁接管、RSS 地址拦截、CSRF 来源校验、NAPSTIC 重试/ID 提取等回归测试。

## v0.3.1 — 发送记录面板（2026-08-10）

### 新增：发送记录
- 控制台新增「发送记录」面板：每次任务运行的 时间 / 类型 / 篇数 / 投递状态 / 日报文件 一览，可按时文献类型过滤、手动刷新；
- 每次运行自动追加结构化记录（`outputs/**/history.jsonl`），状态区分 ✅已送达 / ⏭无记录跳过 / ❌未送达；空结果记录为跳过，避免把抓取失败误报为已发送；
- 兼容显示升级前的历史日报（从 `records_*.json` 还原篇数，标记为「📄历史日报」）。

## v0.3.0 — 多模型来源 + LLM 连接测试 + 无窗口启动（2026-08-10）

### 新增：模型来源下拉（31 项）
- 「模型来源」下拉扩充至 31 项，按 国内官方 / 国际官方 / 聚合中转 / 本地自建 / 自定义 分组；
- 选择来源后自动填写 API 地址与模型名（含 `chat/completions` 后缀），仅需填写 API Key；
- 适配 OpenAI 兼容网关（DeepSeek、智谱、通义、Kimi、Gemini、Ollama、OneAPI 等）。

### 新增：测试模型连接
- 配置台新增「测试模型连接」按钮与 `/api/llm/test` 接口；
- 兼容推理模型（`content` 为空时读取 `reasoning_content`），超时 15s；
- 错误分类提示：401/403（Key 无效或无权访问）、402（余额不足）、404（地址/模型不存在）、429（限流）、连接超时等。

### 修复：启动不再弹出 PowerShell 窗口
- PyInstaller 改为 `--noconsole`（GUI 子系统）打包；
- 计划任务参数加 `-WindowStyle Hidden`；
- 控制台内 PowerShell 子进程统一加 `CREATE_NO_WINDOW` 标志。

### 增强
- 控制台 UI 日志重定向至 `logs/console-ui.log`，便于排查；
- 抓取结果为空时跳过通知发送，避免把抓取失败误报为"今日无文献"。

### 安全
- `.gitignore` 补充 `_internal/` 与 `*.exe`，构建产物与个人凭据均不入库。

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