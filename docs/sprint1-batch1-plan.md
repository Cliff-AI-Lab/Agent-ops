# Sprint 1 第一批 — 项目脚手架 + L1 客户端 + L4 契约

## Context

Agent Harness 是一个对话式 Meta-Agent 平台,用户一句话 SOP 生成可部署到睿动沙箱的智能体/工具/UI。核心差异化是**模型漂移抑制** — 同一句话在不同能力 LLM 下产出要稳定。已定的六层架构(L1 模型抽象 / L2 对话 / L3 编排 / L4 稳定性内核 / L5 生成管线 / L6 交付)中,**L4 是稳定性命门**。

本批次是 Sprint 1 的前 3 个任务,目标:把**最底层地基**打好,让后续 L2/L3/L5 都能稳稳地站在上面。不做业务逻辑,只做:脚手架、LLM 入口、契约 Schema。

任务清单:#1 项目脚手架 / #5 L1 睿动 LLM 客户端 / #4 L4 Pydantic 三级契约。

---

## 关键决策(拍板后不改)

### D1. 包管理 → **uv**
理由(本项目专属):
- 锁文件 bit-identical,对"跨模型 parity 测试"的环境可复现是刚需
- FastAPI + pydantic v2 + httpx 的依赖组合,poetry 解析常 30s+,uv ~1s
- monorepo 要同时跑 backend/tests,`uv run` 免激活 venv 体验最连贯
- 风险:企业内网可能拉不到 `astral.sh` → 缓解方案见 §风险

### D2. 用 `openai` SDK,不用裸 httpx
- 先用 SDK 降代码量,**在 `LLMClient` 里包死**,对外不暴露 SDK 类型
- Provider 抽象层预留,parity 测试一旦发现与睿动实际行为有偏差,切裸 httpx(半天内完成)
- kwpy 走裸 httpx 是因为它早期极简,我们业务复杂度不同

### D3. `CodeArtifact` MVP 用扁平文件列表
- `files: list[{path, content}]` + `dependencies: dict` + `entrypoint: str`
- 已知缺陷(Sprint 2-3 再改):几十文件/二进制/增量更新会爆
- 迁移路径:引入 `ArtifactPatch(op=create/update/delete)` 或 git tree 引用

### D4. 三套环境变量命名约定
- `SANDBOX_API_BASE` — 睿动网关地址(dev/sandbox 都用 `https://iruidong.com/v1`,prod 由客户配)
- `RUIDONG_API_KEY` — 禁止进源码/git
- `HARNESS_ENV` — `dev` / `sandbox` / `prod`,驱动配置分叉
- `HARNESS_DEFAULT_MODEL` — **不硬编码**,私有化部署由客户配

---

## 目录树(精确到文件)

```
agent-harness/
├── pyproject.toml
├── uv.lock
├── .python-version                 # 3.11
├── .env.dev / .env.sandbox / .env.prod
├── .env.example                    # 提交 git 的示例
├── .gitignore
├── README.md
├── backend/
│   └── app/
│       ├── __init__.py
│       ├── main.py                 # FastAPI 入口 + /health
│       ├── config.py               # pydantic-settings 加载 env
│       ├── core/
│       │   ├── llm/
│       │   │   ├── client.py       # LLMClient(任务 #5 主体)
│       │   │   ├── errors.py       # 错误分层
│       │   │   ├── retry.py        # 指数退避+jitter
│       │   │   └── model_filter.py # /v1/models 客户端过滤
│       │   └── stability/
│       │       ├── contracts.py    # 三级契约(任务 #4 主体)
│       │       └── examples.py     # 每级 3+ few-shot 示例
│       └── db/
│           ├── schema.sql          # sessions / model_profiles
│           └── init_db.py          # aiosqlite 初始化
├── frontend/
│   └── README.md                   # "本批次不实现,Sprint 1 后期启动"
├── docs/architecture.md            # 六层架构占位
├── memory/.gitkeep
└── tests/
    ├── conftest.py                 # httpx/respx mock fixture
    ├── test_llm_client.py
    ├── test_contracts.py
    └── test_provider_parity.py     # 改造 kwpy 模板,暂留空壳
```

---

## 关键接口骨架

### `core/llm/errors.py` — 错误分层(借鉴 Codex retry.rs)

```python
class LLMError(Exception): ...
class RateLimitError(LLMError):              # 429 → 重试
    def __init__(self, msg, retry_after=None): ...
class NetworkError(LLMError): ...            # 连接/超时/5xx → 重试
class InvalidResponseError(LLMError): ...    # 4xx/解析失败 → 向上抛,不重试
class ConfigError(LLMError): ...             # 缺 API Key 等 → 向上抛
```

### `core/llm/client.py`

```python
class ChatMessage(BaseModel):
    role: Literal["system","user","assistant","tool"]
    content: str

class ModelInfo(BaseModel):
    id: str
    is_chat: bool

class LLMClient:
    """OpenAI-compatible async client bound to Ruidong gateway.
    Reads SANDBOX_API_BASE + RUIDONG_API_KEY from env. Never hardcoded.
    Layered retry: 429/5xx exp-backoff+jitter; 4xx raise immediately."""

    def __init__(self, base_url=None, api_key=None, timeout=60.0, max_retries=3): ...
    async def list_models(self) -> list[ModelInfo]: ...        # 客户端过滤
    async def chat(self, model, messages, *, tools=None, temperature=0.2) -> ChatMessage: ...
    async def chat_stream(self, model, messages, *, tools=None) -> AsyncIterator[dict]: ...
    async def aclose(self) -> None: ...
```

### `core/stability/contracts.py` — L4 三级契约

```python
class RequirementSpec(BaseModel):
    """L1: 对话澄清后的用户需求结构化"""
    product_name: str          = Field(..., max_length=32)
    target_users: list[str]    = Field(..., min_length=1)
    core_pages: list[str]      = Field(..., min_length=1)
    reference_brands: list[str] = Field(default_factory=list)
    special_requirements: list[str] = Field(default_factory=list)

class PageBlueprint(BaseModel):
    route: str; title: str
    components: list[Literal["hero","list","form","card-grid","table"]]
    data_fields: list[str]

class BrandTokens(BaseModel):
    primary: str; font: str
    radius: Literal["none","sm","md","lg"]

class UIBlueprint(BaseModel):
    """L2: RequirementSpec → UI 蓝图"""
    pages: list[PageBlueprint] = Field(..., min_length=1)
    brand: BrandTokens

class FileEntry(BaseModel):
    path: str; content: str

class CodeArtifact(BaseModel):
    """L3: 可部署产出"""
    files: list[FileEntry]        = Field(..., min_length=1)
    dependencies: dict[str, str]  = Field(default_factory=dict)
    entrypoint: str
```

每个契约带 `model_config = {"json_schema_extra": {"examples": [...]}}`,示例集中放在 `examples.py`,供 few-shot prompt 使用。

---

## 依赖(`pyproject.toml`)

```toml
[project]
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115", "uvicorn[standard]>=0.30",
  "pydantic>=2.7", "pydantic-settings>=2.3",
  "httpx>=0.27", "openai>=1.40",
  "aiosqlite>=0.20", "jinja2>=3.1",
  "sse-starlette>=2.1", "python-dotenv>=1.0",
]
[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21", "ruff>=0.5"]
[project.scripts]
harness-api = "app.main:run"
harness-initdb = "app.db.init_db:main"
```

---

## SQLite 初始 schema

```sql
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
  user_sop TEXT, status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS model_profiles (
  model_id TEXT PRIMARY KEY, family TEXT, context_window INT,
  drift_score REAL, last_probed_at TEXT);
```

---

## 参考文件(借鉴不复制)

- `E:\软通AI产品\AI-lab产品\agent Harness\kwpy\tests\test_provider_parity.py` — parity 测试模式,直接改造到 `tests/test_provider_parity.py`(本批次只留空壳,Sprint 1 后期填)
- `E:\软通AI产品\AI-lab产品\agent Harness\kwpy\src\kwpy\ai\types.py` — 中间类型命名参考
- `E:\软通AI产品\AI-lab产品\agent Harness\kwpy\pyproject.toml` — 依赖风格参考
- `E:\frontend-design\_unzipped\design-workflow\SKILL.md` — 后续 L5 UI 管线的标准(本批次不触)

---

## 验收标准(全绿才算完成)

### 任务 #1 脚手架
- `uv sync` 成功
- `uv run harness-initdb` 生成 `harness.db`,含 `sessions` + `model_profiles` 两表
- `uv run uvicorn app.main:app --reload` 起服务
- `GET /health` 返回 `{"status":"ok","env":"dev"}`
- 缺 `RUIDONG_API_KEY` 时启动不崩,仅调 LLM API 时抛 `ConfigError`
- `.env.*` 模板齐,`.env.example` 进 git,真 env 进 `.gitignore`

### 任务 #5 L1 客户端
`pytest tests/test_llm_client.py` 全绿,覆盖:
- `list_models()` 用 respx mock 返回混合列表,断言 embed/tts/whisper 被过滤
- 429 触发重试,断言调用次数 + 指数退避
- 4xx/解析错误**不重试**,直接抛 `InvalidResponseError`
- 无 API Key 构造客户端抛 `ConfigError`
- 源码 `grep iruidong.com` 只出现在 README/`.env.example`,核心代码纯从 env 读

### 任务 #4 L4 契约
`pytest tests/test_contracts.py` 全绿:
- 三级契约各 3+ 示例进 `model_json_schema()["examples"]`
- 非法 JSON(缺字段/类型错)抛 `ValidationError`
- `.model_json_schema()` 可序列化为 dict,含 `required` 与 `properties`
- 能从合法 JSON 反序列化回 Python 对象

---

## 风险

| 风险 | 可能性 | 缓解 |
|---|---|---|
| `CodeArtifact` 扁平结构无法扩展到多文件/二进制 | 中 | Sprint 2-3 迁移到 `ArtifactPatch` 增量模型 |
| 企业内网拉不到 uv | 中 | `pyproject.toml` 是标准 PEP 621,切 poetry 半天完成 |
| openai SDK 与睿动 SSE 细节有差异 | 低-中 | `LLMClient` 包死 SDK,parity 测试暴露后切裸 httpx |
| Pydantic Schema 给弱 LLM 依然太复杂 | 低 | 任务 #4 完成后跑一次 Haiku 级模型 drill,发现问题在示例上加 few-shot |

---

## 下一批(Sprint 1 Batch-2,参考)

完成本批次后继续:#8 ModelProfile 能力探测 → #10 Validator+Repair → #6 接待员 Agent → #2 Meta-Agent MVP。最后 Batch-3 做 #3 UI 管线 + #9 打包 + #7 前端 + #11 Parity 测试。
