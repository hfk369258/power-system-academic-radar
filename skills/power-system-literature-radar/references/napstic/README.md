# cn-abstracts-tool — 中文核心期刊摘要抓取工具

为「文献雷达」补充中国核心期刊（CNKI 系）论文的题录 + 摘要数据。

## 为什么用「国家学术搜索」(NAPSTIC) 而不是直接爬知网

| 方案 | 结论 |
|---|---|
| **爬知网 (kns.cnki.net)** | ❌ 不推荐。知网无公开 API；2024 年知网起诉秘塔 AI（仅抓摘要/题录即被要求下架）；2025 年修订《反不正当竞争法》新增数据条款，绕过反爬批量抓取的法律风险显著上升。 |
| **万方 / 维普** | ⚠️ 万方文章页有反爬（403/412/验证码），无通用"文摘"开放 API（仅付费的万方选题 API）。可用但需人工/半自动。 |
| **期刊官网** | ⚠️ 部分官网有反爬（如 aeps-info.com 返回 412）或不可达，逐刊定制、易碎。 |
| **OpenAlex / Crossref** | ⚠️ 英文元数据覆盖了部分中文刊，但摘要覆盖率极低（1%~8%），且电网技术等刊存在被掠夺性克隆污染的数据。 |
| **✅ NAPSTIC (search.napstic.cn)** | **推荐为主数据源。** 中国科学技术信息研究所(ISTIC)运营的国家级开放平台；期刊方自愿公开摘要/DOI/中英文题名；服务端渲染无验证码；一次请求即可拿到整期目录+中文摘要。 |

## 覆盖情况（已实测，2026-07）

| 期刊 | slug | 年份范围 | 最新完整年 |
|---|---|---|---|
| 中国电机工程学报 | `zgdjgcxb` | 1998~2024 | 2024（23期） |
| 电力系统自动化 | `dlxtzdh` | 1978~2025 | 2024（2025 仅第1期） |
| 电网技术 | `dwjs` | 1998~2024 | 2024 |
| 高电压技术 | `gdyjs` | 1975~2024 | 2024（12期） |
| 电工技术学报 | `dgjsxb` | 1999~2025 | 2024（2025 仅第2期） |
| 电力系统保护与控制 | `jdq` | 2000~2025 | 2024（2025 仅第1期） |
| 电力自动化设备 | `dlzdhsb` | 1999~2025 | 2024（2025 仅第1期） |
| 中国电力 | `zgdl` | 1983~2024 | 2024（12期） |
| 电力建设 | `dljs` | 2000~2025 | 2024（2025 仅第1期） |
| 华北电力大学学报 | `hbdldxxb` | 1974~2024 | 2024（6期） |
| 现代电力 | `xddl` | 1996~2024 | 2024 |

> ⚠️ **新鲜度提醒**：期刊页数据滞后约 0.5~1.5 年（最新完整年到 2024，部分刊有 2025 零星期）。
> 但 `search` 子命令用的**检索接口**（聚合万方渠道）更新——网络首发论文的 `online_date` 能到当前月（实测有 2026-03 的论文），**跟前沿请优先用 `search`**。
>
> 英文姊妹刊（可用你已有的 IEEE/Springer API）可补充最新英文动态：CSEE JPES（IEEE）、Protection and Control of Modern Power Systems（**IEEE**，2024 年起从 SpringerOpen 迁入，DOI 前缀 10.23919/PCMP）、Journal of Modern Power Systems and Clean Energy（Springer）。
> ⚠️ 英文刊是独立投稿的英文期刊、**非中文论文翻译版**，仅覆盖中文刊约 5~10% 的精华子集；完整跟踪中文论文仍需 NAPSTIC。
> 📌 **Crossref 排除**：已实测中国电力期刊的 DOI（10.13334/10.13335/10.13336/10.16081）都注册在 **ISTIC 中国DOI系统**，不在 Crossref 上——Crossref API 拿不到这些中文刊的任何数据，勿作数据源。

## 使用

```bash
# 1. 查看某期刊有哪些年份/期数
python cn_napstic.py list zgdjgcxb

# 2. 抓某一期（快，仅中文摘要）
python cn_napstic.py fetch zgdjgcxb --year 2024 --issue 17 --out issue17.json

# 3. 抓某一期并逐篇补全详情（含英文标题/摘要、DOI、关键词、作者单位）——慢
python cn_napstic.py fetch zgdjgcxb --year 2024 --issue 17 --full --out issue17_full.json

# 4. 抓最近几个月（适合雷达定时增量）
python cn_napstic.py recent dlxtzdh --months 3 --full --out recent.json

# 5. 抓配置里所有期刊
python cn_napstic.py recent-all --months 3 --full --out all.json

# 6. ★ 按关键词检索全部中文期刊（检索接口数据更新，含网络首发日期）
python cn_napstic.py search "构网型" --size 50 --out grid-forming.json
python cn_napstic.py search "储能" --pages 5 --size 20 --journal "电力系统自动化" --out ess.json
```

`--delay 秒数` 可调请求间隔（默认 1.2s），请对公共服务保持礼貌、低频抓取。

> **给雷达的建议**：`search` 子命令是跟前沿最直接的方式——按你的研究方向（如"构网型""储能寿命""分布式光伏"等）每周拉一次，
> 按 `online_date`（网络首发/上线日期）排序取增量，即可覆盖全网中文期刊（含万方渠道数据），比按期刊逐期抓更全更及时。

## 输出 JSON 字段（--full 模式）

```json
{
  "source": "napstic",
  "source_id": "zgdjgcxb202417002",
  "journal_cn": "中国电机工程学报",
  "journal_en": "Proceedings of the CSEE",
  "issn": "0258-8013",
  "year": "2024", "volume": "44", "issue": "17", "pages": "6707-6720",
  "title_cn": "\"双碳\"目标下我国能源电力系统发展趋势分析:绿电替代与绿氢替代",
  "title_en": "Analysis of the Development Trend of ...",
  "authors_cn": ["周孝信", "赵强", "张玉琼", "杨宏华"],
  "affiliations_cn": ["中国电力科学研究院有限公司,北京市海淀区 100192"],
  "keywords_cn": ["绿电替代", "绿氢替代", "…"],
  "keywords_en": ["green electricity substitution", "…"],
  "abstract_cn": "能源电力系统的低碳转型是…",
  "abstract_en": "The low-carbon transition of energy and power systems is…",
  "doi": "10.13334/j.0258-8013.pcsee.240634",
  "detail_url": "https://search.napstic.cn/literature/periodical/010zgdjgcxb202417002"
}
```

## 接入文献雷达

与 arXiv / IEEE / Elsevier 源合并时，按 **DOI 去重** 即可（英文姊妹刊与中文原文可能对应不同 DOI，可另行用标题/作者 fuzzy 匹配关联中英版本）。

示例（伪代码）：

```python
import cn_napstic
records = cn_napstic.fetch_recent("zgdjgcxb", months=3, fetch_details=True)[0]
# records 已经是统一 schema，可直接并入你的文献雷达数据库
# dedup: seen = {r["doi"] for r in existing}; new = [r for r in records if r["doi"] not in seen]
```

## 合规说明

- NAPSTIC 由中信所运营，期刊方主动向平台提供公开元数据与摘要；抓取的是**公开可见**的题录摘要，非绕过付费墙。
- 请仅用于**个人科研追踪**，低频少量抓取，不批量下载全文、不对外转发、不用于商业。
- 遵守平台 robots 与访问频率；脚本默认 1.2s 节流。
