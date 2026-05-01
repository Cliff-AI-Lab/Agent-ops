# Web 研究助手 (`agents.web_research`)

> tier: **agent** · version: **0.1.0** · status: **active**

互联网研究 Agent。给定一个研究主题或问题,先用 `web.search.tavily` 找相关网页 URL,
再用 `web.scrape.firecrawl` 拉核心内容,最后用 LLM 综合输出**结构化研究纪要**
(关键发现 + 证据链接 + 反对观点)。

## 适用场景

| 场景 | 典型用法 |
|---|---|
| 竞品分析 | 「帮我对比 Dify 和 Coze 在企业 RAG 场景的优劣势」 |
| 技术选型 | 「给我评估 Cursor / Claude Code / Cline 在 Windows 下的体验」 |
| 综述报告 | 「2026 年主流 AI Agent 框架综述」 |
| 新闻事件梳理 | 「最近一周 OpenAI 在企业市场的动态」 |

## 输入

```json
{
  "topic": "调研主题",
  "depth": "shallow | normal | deep",
  "language": "zh | en"
}
```

## 输出结构

```json
{
  "summary": "三段式纪要主体",
  "findings": ["关键发现 1", "关键发现 2", "..."],
  "sources": [{ "url": "...", "title": "..." }]
}
```

## 组合关系(三层金字塔)

- **原子层**(直接调用):
  - `web.search.tavily` — Tavily 搜索 API
  - `web.scrape.firecrawl` — Firecrawl 网页抓取
- **组合层**(本 agent **未**复用 capability,直接调原子层 — 这是 atomic-agent 模式)
- **智能体层**:本 agent

## 运行参数

| key | 默认 | 说明 |
|---|---|---|
| `search_top_k` | 5 | Tavily 每个 sub-query 拉几条结果 |
| `scrape_max_chars` | 8000 | 单页最多保留多少正文字符给 LLM |
| `summarizer_model_pref` | heavy | 让 IntentRouter 优先派最强模型(Sonnet/Opus 级) |

## 限制

- 单次调用预算 **$0.30**,深度 deep 时可能超(届时降级到 normal)
- 不支持登录后才能访问的页面(Firecrawl 适配器尚未支持 cookie/auth)
- 中文内容 LLM 输出语言默认中文,可由 `language` 字段覆盖

## 调用方式

通过 IntentRouter 自动选中(意图触发关键词见 YAML),或显式:

```bash
ops agent run agents.web_research --input '{"topic": "Dify vs Coze 对比", "depth": "normal"}'
```
