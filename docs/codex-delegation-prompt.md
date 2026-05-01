# Codex Delegation Prompt — Sprint 1 Batch-1

你是被 Claude 委派的代码生成助手,严格按下方规则执行。

## 绝对规则

1. **只能在当前工作目录内创建/修改文件**,禁止触碰 workspace 外任何文件
2. **必须严格遵循 `docs/sprint1-batch1-plan.md` 中的规格**,所有目录树/接口/依赖/环境变量必须与 plan 完全一致
3. **不准添加 plan 规格之外的文件、依赖、功能**(包括:Docker、CI 配置、pre-commit、额外的 LLM provider)
4. **不准改动 `docs/sprint1-batch1-plan.md` 和本文件本身**
5. **不要执行 `uv sync` / `pip install` / `pytest` / `uv run` 等命令** —— 只生成文件,验证由 Claude 来做

## 任务

读取 `docs/sprint1-batch1-plan.md`,按其中的"目录树"、"关键接口骨架"、"依赖清单"、"SQLite 初始 schema"、"环境变量"各章节,创建全部文件。

## 必须创建的文件清单(约 30 个,以下清单为准;`uv.lock` **不要创建**,由 `uv sync` 后续生成)

**根目录配置(8):**
- `pyproject.toml` — 依赖与 scripts 严格按 plan
- `.python-version` — 内容:`3.11`
- `.env.dev` / `.env.sandbox` / `.env.prod` — 按 plan §5 的表,实际值用占位符(`YOUR_KEY_HERE`)
- `.env.example` — 所有 key 都用占位符,加注释
- `.gitignore` — Python + uv + IDE + `.env.*`(保留 `.env.example`)+ `harness.db`
- `README.md` — 项目定位、快速启动步骤、目录说明,简洁即可

**backend/app/(3):**
- `backend/app/__init__.py` — 空
- `backend/app/main.py` — FastAPI 入口,`/health` 返回 `{"status":"ok","env":"<HARNESS_ENV>"}`
- `backend/app/config.py` — `pydantic-settings` 加载 env,定义 `Settings` 类包含所有 plan §5 的 key

**backend/app/core/llm/(5):**
- `__init__.py` — 导出 `LLMClient`
- `client.py` — 按 plan 接口骨架完整实现,用 `openai.AsyncOpenAI` 封装;`list_models` 用 `model_filter` 过滤;`chat` / `chat_stream` 走 retry
- `errors.py` — 4 个异常类按 plan
- `retry.py` — `async def with_retry(fn, *, max_retries, base_delay, max_delay)`,指数退避 + full jitter,**只重试 `RateLimitError` / `NetworkError`**,其他直接抛
- `model_filter.py` — `is_chat_model(model_id: str) -> bool`,过滤 embed/tts/whisper/image/audio 等关键字,规则要能被测试

**backend/app/core/stability/(3):**
- `__init__.py` — 导出所有契约
- `contracts.py` — 三级契约严格按 plan 的字段和类型,每个 BaseModel 带 `model_config = {"json_schema_extra": {"examples": [...]}}`
- `examples.py` — 每个契约至少 3 个完整示例,契约 import 时合并进 `json_schema_extra`

**backend/app/db/(3):**
- `__init__.py` — 空
- `schema.sql` — plan §6 的建表 SQL
- `init_db.py` — 读 `schema.sql` + `aiosqlite` 执行建表;提供 `main()` 用于 `harness-initdb` 脚本入口

**tests/(5):**
- `__init__.py` — 空
- `conftest.py` — `respx` mock fixture 为 `https://iruidong.com/v1` 和 env 注入
- `test_llm_client.py` — 覆盖 plan 验收标准:`list_models` 过滤、429 重试次数断言、4xx 不重试抛 `InvalidResponseError`、缺 API Key 抛 `ConfigError`
- `test_contracts.py` — 覆盖 plan 验收标准:examples ≥ 3、非法 JSON 抛 `ValidationError`、`model_json_schema()` 含 `required` + `properties`、合法 JSON 能反序列化
- `test_provider_parity.py` — 空壳,加一个 `@pytest.mark.skip(reason="Sprint 1 Batch-3 实现")` 的函数和引用 `E:\软通AI产品\AI-lab产品\agent Harness\kwpy\tests\test_provider_parity.py` 的注释

**占位(3):**
- `frontend/README.md` — "本批次不实现,Sprint 1 后期启动,届时用 Next.js 14 + shadcn/ui"
- `docs/architecture.md` — 六层架构占位(L1/L2/L3/L4/L5/L6 各一段描述)
- `memory/.gitkeep` — 空文件

## 实现要点

- 所有 Python 文件第一行 `from __future__ import annotations`
- Python 代码用 4 空格缩进,类型注解齐全
- 不写多行 docstring,类/函数一行 docstring 即可
- 不写注释解释显而易见的代码
- `openai` SDK 的 AsyncOpenAI 实例化时传 `base_url` 和 `api_key`
- 错误映射:`openai.RateLimitError` → `RateLimitError`,`openai.APIConnectionError`/`openai.APITimeoutError` → `NetworkError`,`openai.BadRequestError`/解析失败 → `InvalidResponseError`
- `with_retry` 用 `asyncio.sleep` + `random.uniform(0.9, 1.1)` 做 jitter
- `model_filter.is_chat_model`:过滤关键字 `embed` / `embedding` / `tts` / `whisper` / `image` / `audio` / `speech` / `vision` 等,返回 False
- 契约里 `BrandTokens.primary` 用 regex 验证 hex(`^#[0-9a-fA-F]{6}$`)
- pytest 使用 asyncio_mode=auto(已在 pyproject 里)

## 完成后

1. 用 `find . -type f -not -path './.git/*' | sort` 列出所有文件(相对路径)
2. 输出一份 150 字以内的交付总结:创建了多少文件、最关键的设计决策、1 个潜在问题(如果有)
3. 不要执行任何验证命令

现在读 plan 并开始。
