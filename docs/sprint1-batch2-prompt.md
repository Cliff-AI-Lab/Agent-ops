# Sprint 1 Batch-2 Codex Delegation Prompt

## 背景

Batch-1 已完成:脚手架 + L1 睿动客户端(`backend/app/core/llm/`) + L4 契约(`backend/app/core/stability/contracts.py` + `examples.py`)。所有测试通过。

Batch-2 要做 4 个任务:**#10 Validator + Repair → #8 ModelProfile 探测 → #6 接待员 Agent + 状态机 → #2 Meta-Agent MVP**。

## 绝对规则

1. 只能在当前工作目录内创建/修改文件
2. **不准改动 Batch-1 已有文件**(`client.py` / `errors.py` / `retry.py` / `model_filter.py` / `contracts.py` / `examples.py` / `config.py` / `main.py` / `.env.*` / `pyproject.toml`)
   - 例外:`main.py` 可以**增加**路由注册,但不能改已有 `/health`
   - 例外:`pyproject.toml` 禁止改
3. 不要执行 `uv sync` / `pytest` / 启动服务(我来验证)
4. 不准添加超出本 prompt 范围的文件或功能
5. 本文件自身不要修改

## 任务 #10:L4 Validator + Repair(基础设施,先做)

### 文件:`backend/app/core/stability/validator.py`

```python
from __future__ import annotations
from typing import Type, TypeVar
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

class SchemaValidator:
    """Validates raw LLM text output against a Pydantic schema."""

    def __init__(self, schema: Type[T]) -> None: ...

    def parse(self, raw: str) -> T:
        """Parse raw text. Tries JSON first; raises ValidationError on failure."""

    def format_error(self, err: ValidationError) -> str:
        """Render ValidationError into a short, model-digestible feedback string
        (≤ 400 chars) listing missing/invalid fields with path + message."""
```

### 文件:`backend/app/core/stability/fallback.py`

```python
from __future__ import annotations
from pathlib import Path
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "fallback"

def load_fallback(schema: Type[T]) -> T:
    """Load a minimal valid instance for the given schema.
    Looks up `TEMPLATES_DIR / f"{schema.__name__}.json"`.
    Raises FileNotFoundError if no fallback defined (intentional — caller decides)."""
```

创建兜底模板文件(有效最小 JSON,能通过 Pydantic 校验):
- `backend/app/templates/fallback/RequirementSpec.json`
- `backend/app/templates/fallback/UIBlueprint.json`
- `backend/app/templates/fallback/CodeArtifact.json`

### 文件:`backend/app/core/stability/repair.py`

```python
from __future__ import annotations
from typing import Awaitable, Callable, Type, TypeVar
from pydantic import BaseModel, ValidationError
from app.core.llm.client import LLMClient, ChatMessage
from app.core.stability.validator import SchemaValidator
from app.core.stability.fallback import load_fallback

T = TypeVar("T", bound=BaseModel)

class RepairResult:
    """Outcome of generate_with_repair."""
    def __init__(self, value: T, attempts: int, used_fallback: bool,
                 last_errors: list[str]) -> None: ...

async def generate_with_repair(
    llm: LLMClient,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    max_repairs: int = 3,
    temperature: float = 0.2,
) -> RepairResult:
    """Generate output that satisfies `schema`.

    Error-layering contract (CRITICAL):
    - Network/RateLimit errors propagate up from llm.chat (already retried inside client).
      These are NEVER fed back to the model as "repair" messages.
    - ValidationError (schema fail) triggers a repair turn: error summary is appended
      as a user message, model retries. Up to `max_repairs` attempts.
    - If all repairs fail, load fallback template and return with used_fallback=True.
    - The two paths MUST NOT mix.
    """
```

**实现要点:**
- 每次 repair 尾部追加 `ChatMessage(role="user", content=f"Previous output failed validation. Fix these issues and respond with JSON only:\n{error_summary}")`
- 系统 prompt 自动注入 `"Return JSON matching this schema:\n{schema.model_json_schema()}"` + `examples` 若干
- 捕获 `ValidationError` → 继续;捕获 `RateLimitError` / `NetworkError` → 向上抛
- 兜底触发时记录 `last_errors`,供调用方感知

### 文件:`tests/test_repair.py`

用 respx 或自建假 LLM 类,覆盖:
1. 第 1 次返回合法 JSON → `attempts=1, used_fallback=False`
2. 第 1 次返回非法 JSON,第 2 次返回合法 → `attempts=2, used_fallback=False`,错误被回喂(检查第 2 次请求的 messages 包含错误反馈)
3. 3 次都失败 → `used_fallback=True`,value 来自模板
4. RateLimitError 直接上抛,不走 repair
5. SchemaValidator.format_error 输出 ≤ 400 字符

---

## 任务 #8:ModelProfile 能力探测

### 文件:`backend/app/core/llm/profile.py`

```python
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field

class ModelProfile(BaseModel):
    """Capability probe result for a given model."""
    model_id: str
    family: str = ""                  # e.g. "opus" / "sonnet" / "haiku" / "unknown"
    context_window: int = 0
    json_reliability: float = 0.0     # 0-1, % of JSON schema probes passed
    function_calling_ok: bool = False
    long_context_ok: bool = False
    chinese_quality: float = 0.0      # 0-1 heuristic score
    needs_schema_reminder: bool = True
    recommended_temperature: float = 0.2
    max_retry: int = 3
    last_probed_at: datetime = Field(default_factory=datetime.utcnow)

PROBES: list[dict] = [
    {"name": "json_schema_compliance", ...},
    {"name": "function_calling", ...},
    {"name": "long_context_8k", ...},
    {"name": "chinese_generation", ...},
]  # 4 个探针配置:prompt + 校验函数

class ProfileProber:
    def __init__(self, llm: LLMClient, db_path: str | None = None) -> None: ...
    async def probe(self, model_id: str, *, force: bool = False) -> ModelProfile:
        """Run all probes. If cached profile exists in SQLite and not force, return cached.
        Otherwise run probes, store profile into `model_profiles` table, return it."""
    async def get_cached(self, model_id: str) -> ModelProfile | None: ...
```

**实现要点:**
- 从 `app.config.get_settings().harness_env` 判断 DB 路径(`harness.db`)
- 探针不可用(无 API Key / 模型不存在)时:返回 `ModelProfile(json_reliability=0, function_calling_ok=False, ...)` 的保守 default,**不抛异常**
- 每个探针独立失败不中断其他;汇总成 profile

### 文件:`tests/test_profile.py`

用 mock LLM(一个实现 chat 接口的假类,不经过 respx):
1. 所有探针返回合法 → profile 分数 ≈ 1.0
2. JSON 探针失败 → json_reliability 下降,needs_schema_reminder=True
3. 缓存命中不再调 llm(用 call counter)
4. RateLimitError 中途抛出 → 不崩,部分分数保留
5. family 推断:`gpt-4o` → 空字符串或"unknown"(不硬编码 opus/sonnet/haiku 到特定厂商)

---

## 任务 #6:L2 接待员 Agent + 对话状态机

### 文件:`backend/app/core/dialog/state_machine.py`

```python
from __future__ import annotations
from enum import Enum

class DialogState(str, Enum):
    COLLECTING = "collecting"      # 初始收集用户一句话 SOP
    CLARIFYING = "clarifying"      # 多轮澄清
    CONFIRMING = "confirming"      # 向用户确认 RequirementSpec
    GENERATING = "generating"      # 执行 L5 生成(Batch-3 才实现,MVP 直接到 delivered)
    DELIVERED = "delivered"

VALID_TRANSITIONS: dict[DialogState, set[DialogState]] = {
    DialogState.COLLECTING:  {DialogState.CLARIFYING, DialogState.CONFIRMING},
    DialogState.CLARIFYING:  {DialogState.CLARIFYING, DialogState.CONFIRMING},
    DialogState.CONFIRMING:  {DialogState.CLARIFYING, DialogState.GENERATING},
    DialogState.GENERATING:  {DialogState.DELIVERED},
    DialogState.DELIVERED:   set(),
}

class InvalidTransition(Exception): ...

def transition(current: DialogState, target: DialogState) -> DialogState:
    """Raise InvalidTransition if target not allowed."""
```

### 文件:`backend/app/core/dialog/session.py`

```python
from __future__ import annotations
import uuid, json
from datetime import datetime
from pydantic import BaseModel, Field
from app.core.dialog.state_machine import DialogState
from app.core.llm.client import ChatMessage

class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    state: DialogState = DialogState.COLLECTING
    messages: list[ChatMessage] = Field(default_factory=list)
    requirement_spec: dict | None = None   # 填充后的 RequirementSpec JSON

class SessionStore:
    """SQLite-backed session persistence."""
    def __init__(self, db_path: str) -> None: ...
    async def create(self) -> Session: ...
    async def get(self, session_id: str) -> Session | None: ...
    async def save(self, session: Session) -> None: ...
```

**需要扩展 `backend/app/db/schema.sql`** — 给 `sessions` 表加列:`state TEXT`, `messages_json TEXT`, `requirement_spec_json TEXT`。先 DROP 再 CREATE 即可(MVP,无迁移)。

### 文件:`backend/app/core/dialog/receptionist.py`

```python
from __future__ import annotations
from typing import AsyncIterator
from app.core.dialog.session import Session, SessionStore
from app.core.dialog.state_machine import DialogState, transition
from app.core.llm.client import LLMClient, ChatMessage
from app.core.stability.contracts import RequirementSpec
from app.core.stability.repair import generate_with_repair

RECEPTIONIST_SYSTEM_PROMPT = """你是 Agent Harness 的接待员。
职责: 理解用户一句话 SOP,通过多轮对话澄清缺失信息。不要自己编造、不要生成代码。
每轮只问 1-2 个问题。当信息足够时输出确认摘要。
"""

class Receptionist:
    def __init__(self, llm: LLMClient, store: SessionStore, default_model: str) -> None: ...

    async def start(self, user_sop: str) -> Session:
        """Create session in COLLECTING state with user's initial SOP."""

    async def turn(self, session_id: str, user_msg: str) -> AsyncIterator[dict]:
        """Advance dialog one turn. Yields SSE-shaped events:
        - {"type": "state", "value": <new state>}
        - {"type": "token", "value": <text delta>}
        - {"type": "turn_end"}
        State machine logic:
        - If in COLLECTING/CLARIFYING: ask next clarifying question OR transition to CONFIRMING
          (heuristic: after 3+ user turns, try to fill RequirementSpec via generate_with_repair)
        - If in CONFIRMING: if user says "确认/ok/yes" → transition GENERATING (Batch-3 接管)
        """
```

### 文件:`backend/app/api/__init__.py`、`backend/app/api/sessions.py`

FastAPI 路由 + SSE:
- `POST /api/sessions` — body `{sop: string}` → 返回 `{session_id, state}`
- `POST /api/sessions/{id}/turn` — body `{message: string}`,**SSE stream** 返回接待员事件
- `GET /api/sessions/{id}` — 当前状态 + messages

在 `main.py` **追加**(不替换):
```python
from app.api.sessions import router as sessions_router
app.include_router(sessions_router)
```

### 文件:`tests/test_state_machine.py`

- 合法转移全部通过
- 非法转移抛 InvalidTransition
- DELIVERED 无后续

### 文件:`tests/test_session_store.py`

- 用临时 SQLite 文件(tmp_path fixture)
- create / get / save round-trip
- 不存在 id → get 返回 None

### 文件:`tests/test_receptionist.py`

用 mock LLM(类似 test_repair.py 思路):
- start → session state == COLLECTING
- turn 后状态流转正确
- SSE 事件顺序:state → token* → turn_end

---

## 任务 #2:L3 Meta-Agent MVP

### 文件:`backend/app/core/orchestrator/__init__.py`、`meta_agent.py`

```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from app.core.llm.client import LLMClient
from app.core.stability.contracts import RequirementSpec
from app.core.stability.repair import generate_with_repair

Intent = Literal["ui", "agent", "tool", "unknown"]

class IntentResult(BaseModel):
    intent: Intent
    confidence: float

class MetaAgent:
    """Classifies intent, then fills RequirementSpec via L4 repair loop."""

    def __init__(self, llm: LLMClient, light_model: str, heavy_model: str) -> None: ...

    async def classify_intent(self, user_sop: str, history: list[str]) -> IntentResult:
        """Use light_model for intent classification. Return 'ui' for UI generation
        intents. MVP treats all non-ui as 'unknown' (Sprint 2 will expand)."""

    async def build_spec(self, user_sop: str, history: list[str]) -> RequirementSpec:
        """Use heavy_model + generate_with_repair to produce RequirementSpec."""
```

**Router 分层:** `light_model` 用于意图分类,`heavy_model` 用于 Spec 填充。模型名从 `get_settings().harness_default_model` 读(为空时用第一个可用 chat model)。

### 文件:`tests/test_meta_agent.py`

用 mock LLM:
- classify_intent 返回 "ui" 的典型 prompt
- build_spec 返回有效 RequirementSpec
- build_spec 第一次格式错误,第二次通过(走 repair)
- 意图歧义时 confidence < 0.5 → intent == "unknown"

---

## 文件清单汇总(约 20 个新文件)

**稳定性内核 (3):**
- `backend/app/core/stability/validator.py`
- `backend/app/core/stability/repair.py`
- `backend/app/core/stability/fallback.py`

**兜底模板 (3):**
- `backend/app/templates/__init__.py`(空)
- `backend/app/templates/fallback/RequirementSpec.json`
- `backend/app/templates/fallback/UIBlueprint.json`
- `backend/app/templates/fallback/CodeArtifact.json`

**LLM 扩展 (1):**
- `backend/app/core/llm/profile.py`

**对话层 (4):**
- `backend/app/core/dialog/__init__.py`
- `backend/app/core/dialog/state_machine.py`
- `backend/app/core/dialog/session.py`
- `backend/app/core/dialog/receptionist.py`

**编排层 (2):**
- `backend/app/core/orchestrator/__init__.py`
- `backend/app/core/orchestrator/meta_agent.py`

**API 路由 (2):**
- `backend/app/api/__init__.py`
- `backend/app/api/sessions.py`

**修改(**追加,不重写**):**
- `backend/app/main.py` — 追加 include_router
- `backend/app/db/schema.sql` — 扩展 sessions 表列

**测试 (6):**
- `tests/test_repair.py`
- `tests/test_profile.py`
- `tests/test_state_machine.py`
- `tests/test_session_store.py`
- `tests/test_receptionist.py`
- `tests/test_meta_agent.py`

## 实现约束

- 所有 Python 文件第一行 `from __future__ import annotations`
- 类型注解齐全,pydantic v2 写法(`model_config = ConfigDict(...)`,`Field(...)`)
- 单行 docstring
- 不写注释解释显而易见的代码
- 测试用 asyncio 自动模式(pyproject 已配),不需额外 marker
- 不引入新依赖
- `asyncio.sleep` 用 monkeypatch 替换,避免测试慢

## 完成后

1. `find backend/app tests backend/app/templates -type f -newer pyproject.toml | sort` 列出新文件
2. 150 字交付总结:创建文件数、最关键设计、1 个潜在问题
3. **不跑**任何测试或服务
