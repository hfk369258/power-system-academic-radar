# Power System Academic Radar

面向电力系统、综合能源、优化调度与相关交叉方向的本地文献雷达。它会从公开学术接口、可选商业数据库接口和手工导出文件中检索文献，完成筛选、去重、中文摘要翻译、DeepSeek 解读，并按计划发送“邮件简报 + 交互式 HTML 附件”。

项目提供中文可视化控制台，不要求维护者直接修改 JSON。所有 API Key、SMTP 授权码和收件人配置只保存在本机，仓库不会包含真实凭据。

## 主要功能

- 多推送方案：可为不同人员建立独立的关键词、邮箱、数据源、推送计划和去重历史。
- 方案增删改查：创建、复制、重命名、切换和安全删除；最多支持 **20 个方案**。
- 分类计划：期刊论文、预印本和会议论文分别设置启停、每天/每周、时间和星期。
- 去重：每个方案使用独立状态文件，只在邮件成功送达后记录已发送文献。
- 中英文阅读：保留英文摘要，并在其后显示中文翻译和结构化研究概括。
- 邮件与附件：正文是当次总体简报，完整逐篇内容放在交互式 HTML 附件中。
- 本机控制台：关键词、邮箱、API、SMTP、来源和计划均可视化维护。

## 为什么上限是 20 个方案

每个方案最多对应“期刊、预印本、会议”3 个 Windows 计划任务。20 个方案即最多 60 个任务，已经足够覆盖小型课题组，同时避免在普通个人电脑上无限创建任务、日志和并发检索。超过 20 个用户时，建议改为服务器队列或数据库部署，而不是继续增加本机计划任务。

## 环境要求

- Windows 10/11；
- PowerShell 5.1 或更高版本；
- Python 3.10 或更高版本；
- 仅使用 JSON 配置时无需第三方 Python 包；使用 YAML 时安装 `PyYAML`。

```powershell
python -m pip install -r scripts\requirements.txt
```

## 快速开始

```powershell
git clone https://github.com/hfk369258/power-system-academic-radar.git
cd power-system-academic-radar
Copy-Item radar.env.example.ps1 radar.env.ps1
powershell -ExecutionPolicy Bypass -File scripts\start_radar_ui.ps1
```

浏览器会打开仅监听 `127.0.0.1` 的控制台。首次使用建议依次完成：

1. 建立或重命名推送方案；
2. 调整关键词和数据源；
3. 填写 DeepSeek、学术接口和 SMTP 信息；
4. 添加收件邮箱；
5. 设置三类文献的推送频率；
6. 点击“保存配置”；
7. 点击“保存并应用计划”。

## 多人推送方案

初次启动会保留兼容方案 `Basic` 与 `Full`。点击控制台顶部的“新增方案”，可以从现有方案复制检索条件，但新方案不会复制 API Key、SMTP 密码等秘密值。

每个方案独立保存：

- 关键词和筛选规则；
- 收件人；
- 分类推送计划；
- 本机凭据文件；
- 已发送文献状态；
- 输出报告目录；
- Windows 计划任务前缀。

删除方案时，系统会先停用该方案的计划任务，并将文件移动到本机回收目录，而不是立即永久删除。至少必须保留一个方案。

## API 与数据源配置

### 无需 API Key 的来源

- **OpenAlex**：公开学术元数据，默认可直接使用；
- **Crossref**：DOI 和出版元数据，默认可直接使用；
- **arXiv**：预印本来源，启用预印本方案时可开启；
- **RSS**：期刊目录订阅，需要在配置中填写期刊官方 RSS 地址。

这些公共服务仍可能限流。请降低单源候选量、错开多个方案的时间，并遵守各服务条款。

### NAPSTIC 中文文献源（CNKI 系核心期刊）

- 数据来自「国家学术搜索」(search.napstic.cn)，由中信所(ISTIC)运营，期刊方自愿公开题录/摘要/DOI，非付费墙绕过；仅限个人科研低频使用，默认节流 1.5s。
- 两种源类型（无需 API Key）：
  - `napstic_search`（默认开启）：按 `keywords.chinese` 关键词组逐词检索全部中文期刊，含网络首发（`online_date`）。该接口只支持相关度排序、无法按日期过滤（实测 `sort`/`order` 参数被忽略），因此不过滤日期窗口：客户端按 `online_date` 降序排列（无日期沉底），**是否重复由已推送状态文件去重**——首次运行会推送当前最相关+最新的一批，之后只累积新上线的文献；
  - `napstic_journals`（默认关闭）：按 `journals` slug 列表逐刊抓最近 `months` 期，覆盖完整但滞后约 0.5~1.5 年；开启前请确认能接受其请求量。
- 中文文献同样受 `journal_filter` 白名单约束（宁缺毋滥）：`chinese_ei` 内置 21 本电力领域核心/主流期刊，不在名单内的中文期刊不会进入推送（可在控制台/JSON 中增删）。
- 中文文献同样受 `journal_filter` 白名单约束，模板已内置 `chinese_ei` 11 本刊（可在控制台/JSON 中增删）。
- 已知坑：中国电力期刊 DOI 注册在 ISTIC 中国DOI系统，**不在 Crossref**（Crossref 拿不到中文刊数据）；英文姊妹刊（CSEE JPES / PCMP / MPCE）为独立英文刊，仅覆盖中文刊约 5~10% 精华，完整跟踪中文论文请用 NAPSTIC。
- 期刊 slug 表与合规说明见 `skills/power-system-literature-radar/references/napstic/`。

### DeepSeek

用途：中文摘要翻译、逐篇研究概括和当次总体简报。文献检索本身不依赖 DeepSeek。

启用后，系统会把候选文献的题名、摘要、来源和关键词发送给 DeepSeek API。请确认这符合你的数据与隐私要求；不要把未公开稿件或敏感内部材料放入自动处理目录。

1. 登录 [DeepSeek 开放平台](https://platform.deepseek.com/)；
2. 在平台创建 API Key 并确保账户有可用额度；
3. 在控制台“本机接口与账号”中填写 `DEEPSEEK_API_KEY`；
4. 当前正式模型为 `deepseek-v4-flash`。DeepSeek 官方文档把 `https://api.deepseek.com` 称为 OpenAI 兼容 `base_url`；本项目的配置字段会被脚本直接作为 Chat Completions 请求地址使用，因此保留完整的 `https://api.deepseek.com/chat/completions`，不要只改成根地址。

也可以改用任何 OpenAI 兼容网关：在 `radar.env.ps1` 里设置 `$env:DEEPSEEK_BASE_URL = "https://网关地址/v1/chat/completions"` 覆盖端点、`$env:DEEPSEEK_MODEL = "模型名"` 覆盖模型（脚本会为 LLM 请求附加浏览器 User-Agent，便于通过部分网关的 UA 校验）。例如 opencodezen 免费档：`https://opencode.ai/zen/v1/chat/completions` + `deepseek-v4-flash-free`。

官方调用说明见 [DeepSeek API Docs](https://api-docs.deepseek.com/zh-cn/guides/function_calling/)。模型名称和计费可能变化，维护时应以官方文档为准。

### Semantic Scholar

大部分接口允许无 Key 使用，但高峰期更容易被限流。建议在 [Semantic Scholar Academic Graph API](https://www.semanticscholar.org/product/api) 提交 API Key 申请，收到后填写 `SEMANTIC_SCHOLAR_API_KEY`。Key 仅用于提高稳定性，不应提交到 GitHub。

### IEEE Xplore

1. 访问 [IEEE Xplore API Getting Started](https://developer.ieee.org/getting_started)；
2. 注册开发者账户；
3. 填写应用用途并申请 API Key；
4. 审核通过后，将 Key 填入 `IEEE_XPLORE_API_KEY`；
5. 在控制台启用 `ieee_xplore` 数据源。

IEEE Key 必须保密，并受用途与调用频率限制。没有 API Key 时，可以从 IEEE Xplore 手工导出 RIS/BibTeX/CSV 放入 `manual_exports/`。

### Elsevier / Scopus

1. 登录 [Elsevier Developer Portal](https://dev.elsevier.com/)；
2. 阅读 API Service Agreement 并创建 API Key；
3. 将 Key 填入 `ELSEVIER_API_KEY`；
4. 在控制台启用 `elsevier_scopus` 数据源。

部分 Scopus 内容或较高配额可能仍要求机构订阅/授权。API Key 不等同于数据库全文权限。

## SMTP 邮件配置

请使用邮箱服务商生成的 **SMTP 授权码/应用密码**，不要填写网页登录密码。

控制台支持以下变量：

| 变量 | 说明 | 示例 |
|---|---|---|
| `RADAR_SMTP_HOST` | SMTP 主机 | `smtp.qq.com` |
| `RADAR_SMTP_PORT` | SMTP 端口 | `465` |
| `RADAR_SMTP_USER` | SMTP 登录账号 | `sender@example.com` |
| `RADAR_SMTP_PASSWORD` | SMTP 授权码 | 不要提交 |
| `RADAR_EMAIL_FROM` | 发件邮箱 | 通常与登录账号一致 |
| `RADAR_EMAIL_TO` | 备用收件人 | 多个地址用逗号分隔 |

常用设置：

- QQ 邮箱：`smtp.qq.com`，端口 `465`，SSL；
- 163 邮箱：`smtp.163.com`，端口 `465`，SSL；
- 其他服务商：按其官方 SMTP 文档填写，也可使用 `587 + STARTTLS`。

优先使用控制台内每个方案的“推送邮箱”列表；列表为空时才使用 `RADAR_EMAIL_TO`。

## 中英配额与分区

- 每次推送按语言独立配额：`profile.daily_target_en`（默认 10）与 `profile.daily_target_zh`（默认 5），宁缺毋滥——某语言不够时少发，不发凑数文献；两份都没有时全部为空则不发送邮件。
- 附件 dashboard 是一个网页两个界面：顶部「语言分区」可一键切换 英文/中文/全部；digest Markdown 也分「英文文献」「中文文献」两章。
- 模板默认 `backfill_enabled: false`（不回头补发历史文献凑数）；需要时可在 JSON 中打开。

## 分类推送与计划任务

每个方案的三类计划互相独立：

- 期刊论文：只发送识别为期刊文章的记录；
- 预印本：只发送 arXiv/posted content 等记录；
- 会议论文：只发送 conference/proceedings 等记录。

修改频率后必须点击“保存并应用计划”，系统才会创建、更新或停用 Windows 计划任务。建议不同方案和不同类型至少错开 5–10 分钟，避免同时请求外部 API。

## 去重机制

系统从 DOI、来源 ID、规范化标题等生成去重键。每个方案拥有独立状态文件，因此：

- 同一方案不会重复发送已经成功推送的论文；
- 不同方案可以各自收到同一篇论文；
- 邮件发送失败时不会写入“已发送”，后续仍会重试；
- 近期文献不足时，可从更长历史窗口补充“从未发送过”的记录。

## 命令行使用

验证配置：

```powershell
python scripts\power_system_radar.py --config assets\power_system_radar_config.json --validate-config
```

预览检索式：

```powershell
python scripts\power_system_radar.py --config assets\power_system_radar_config.json --dry-run
```

单次运行（期刊论文示例）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_radar.ps1 `
  -ConfigPath assets\power_system_radar_config.json `
  -EnvFile radar.env.ps1 `
  -DocumentType journal `
  -EnableEmail
```

## 输出文件

每次运行会生成：

- `digest_*.md`：可阅读的 Markdown 报告；
- `digest_*.html`：邮件正文；
- `dashboard_*.html`：完整交互式附件；
- `records_*.json`：结构化文献记录；
- `logs/`：运行日志。

上述目录以及本机方案、凭据、状态文件均已加入 `.gitignore`。

## 使用边界与注意事项

本项目与 IEEE、Elsevier、CNKI、Semantic Scholar、OpenAlex、Crossref、arXiv、DeepSeek 及各邮箱服务商均无官方隶属关系。MIT License 只覆盖本项目代码，不授予论文全文、摘要数据库、导出记录或第三方商标的再分发权。使用者必须自行遵守接口限速、数据库许可、机构订阅和邮件服务条款。

## 常见问题

**每天是 0 篇**：检查数据源是否启用、关键词是否过窄、日期窗口、期刊白名单和 API 限流；系统会按配置尝试历史回填。

**页面提示无法连接**：关闭旧控制台窗口，再双击快捷方式或重新运行 `start_radar_ui.ps1`。

**邮件失败**：优先检查 SMTP 授权码、SSL/STARTTLS、端口、发件地址和邮箱服务商的安全限制。

**计划没有执行**：在控制台点击“保存并应用计划”，再到 Windows 任务计划程序的 `\Codex\` 目录查看对应任务。

## 许可

[MIT License](LICENSE)
