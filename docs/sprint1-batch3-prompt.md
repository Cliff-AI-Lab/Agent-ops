# Sprint 1 Batch-3 (后端) Codex Delegation Prompt

Batch-1/2 已完成,全部测试通过。本批次实现:**#3 L5 UI 生成管线 / #9 L6 打包交付器 / #11 Parity 测试套件**。前端 #7 由 Claude 另行处理,本次不碰。

## 绝对规则

1. 只能在当前工作目录内创建/修改文件
2. **严禁修改** `pyproject.toml` / `.env.*` / Batch-1/2 已有源码(`core/llm/*` / `core/stability/*` / `core/dialog/*` / `core/orchestrator/*` / `api/sessions.py` / `config.py`)
3. **允许修改**:`backend/app/main.py`(仅追加 include_router)、`tests/test_provider_parity.py`(替换占位)
4. 不跑 `uv sync` / `pytest` / 启动服务(Claude 验证)
5. 不添加本 prompt 范围外文件/功能
6. 本文件自身不要改

---

## 任务 #3:L5 UI 生成管线 Phase 2 + Phase 3

设计目标:把 `UIBlueprint` 转为可部署的 `CodeArtifact`。移植 `E:\frontend-design\_unzipped\design-workflow\SKILL.md` 的 Phase 2 + Phase 3 到 Python 管线。本批次简化:Phase 0 Logo / Phase 1 品牌 跳过,使用 `UIBlueprint.brand` 即可。

### 文件:`backend/app/core/pipelines/__init__.py`(空)

### 文件:`backend/app/core/pipelines/ui/__init__.py`

```python
from __future__ import annotations
from app.core.pipelines.ui.orchestrator import UIPipeline

__all__ = ["UIPipeline"]
```

### 文件:`backend/app/core/pipelines/ui/prompts.py`

纯常量文件,存两个模板:

```python
from __future__ import annotations

PHASE2_HTML_PROTOTYPE_SYSTEM = """你是 UI 原型专家。根据输入的 UIBlueprint,产出 3 个视觉差异明显的 HTML 原型变体。
约束:
- 每个变体用 React + Babel Standalone,unpkg CDN(版本锁定 react@18.3.1 / react-dom@18.3.1 / @babel/standalone@7.29.0)
- 单文件 HTML,内嵌 JSX,Tailwind 用 CDN 版
- 每个变体视觉方向要明显不同(配色密度 / 布局 / 组件风格 至少一个维度差异显著)
- 严禁裸 hex 色;用 Tailwind 语义类
- 交互元素 ≥ 44x44px
- 全部变体共享同一 UIBlueprint 的页面结构,但视觉风格不同
输出 JSON,schema 见 user prompt。"""

PHASE3_PRODUCTION_CODE_SYSTEM = """你是生产代码专家。根据选定的 HTML 原型变体 + UIBlueprint,产出完整的 Vite + React + shadcn/ui 项目文件树。
约束:
- 用 shadcn/ui (Radix + Tailwind v4)组件,不自己造
- Tailwind 用语义色彩 token(不写裸 hex)
- TypeScript 严格模式
- 每页一个 React 组件,用 React Router v6
- 必须产出:package.json / tsconfig.json / tailwind.config.ts / vite.config.ts / index.html / src/main.tsx / src/App.tsx / src/pages/*.tsx / src/components/ui/* (shadcn 放 button/card/input 三个基础即可)
输出 JSON,schema 见 user prompt。"""
```

### 文件:`backend/app/core/pipelines/ui/phase2_prototype.py`

```python
from __future__ import annotations
from pydantic import BaseModel, Field
from app.core.llm.client import LLMClient, ChatMessage
from app.core.stability.contracts import UIBlueprint
from app.core.stability.repair import generate_with_repair, RepairResult
from app.core.pipelines.ui.prompts import PHASE2_HTML_PROTOTYPE_SYSTEM

class PrototypeVariant(BaseModel):
    name: str = Field(..., description="变体名,如 'compact-sidebar'")
    description: str = Field(..., description="一句话风格描述")
    html: str = Field(..., description="完整 HTML 文档,React+Babel 可直接打开")

class Phase2Output(BaseModel):
    variants: list[PrototypeVariant] = Field(..., min_length=3, max_length=3)

async def generate_prototypes(
    llm: LLMClient, *, model: str, blueprint: UIBlueprint, max_repairs: int = 3
) -> RepairResult:
    """Generate 3 HTML prototype variants. Returns RepairResult[Phase2Output]."""
```

**实现要点:** user_prompt 要含 `blueprint.model_dump_json()`、`Phase2Output.model_json_schema()`;调 `generate_with_repair`。

### 文件:`backend/app/core/pipelines/ui/phase3_production.py`

```python
from __future__ import annotations
from app.core.llm.client import LLMClient
from app.core.stability.contracts import UIBlueprint, CodeArtifact
from app.core.stability.repair import generate_with_repair, RepairResult
from app.core.pipelines.ui.prompts import PHASE3_PRODUCTION_CODE_SYSTEM
from app.core.pipelines.ui.phase2_prototype import PrototypeVariant

async def generate_production_code(
    llm: LLMClient, *, model: str, blueprint: UIBlueprint,
    chosen_variant: PrototypeVariant, max_repairs: int = 3
) -> RepairResult:
    """Generate Vite + React + shadcn/ui CodeArtifact. Returns RepairResult[CodeArtifact]."""
```

### 文件:`backend/app/core/pipelines/ui/orchestrator.py`

```python
from __future__ import annotations
from typing import AsyncIterator
from pydantic import BaseModel
from app.core.llm.client import LLMClient
from app.core.stability.contracts import UIBlueprint, CodeArtifact
from app.core.pipelines.ui.phase2_prototype import generate_prototypes, Phase2Output, PrototypeVariant
from app.core.pipelines.ui.phase3_production import generate_production_code

class UIPipelineEvent(BaseModel):
    phase: str           # "phase2" | "phase3" | "completed"
    message: str = ""
    data: dict | None = None

class UIPipelineResult(BaseModel):
    prototypes: Phase2Output
    chosen_variant: PrototypeVariant
    code: CodeArtifact
    fallback_used: bool

class UIPipeline:
    def __init__(self, llm: LLMClient, model: str) -> None: ...

    async def run(self, blueprint: UIBlueprint) -> AsyncIterator[UIPipelineEvent]:
        """Orchestrate Phase2 → pick variant[0] (MVP) → Phase3.
        Yields events for SSE. Final event phase='completed' carries UIPipelineResult in data."""
```

MVP 决策:Phase 2 后**自动选 variants[0]**(后续 UI 会让用户选,这里简化)。

### 文件:`backend/app/api/generate.py`

```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from app.core.stability.contracts import UIBlueprint
# ...

router = APIRouter(prefix="/api/generate", tags=["generate"])

class GenerateRequest(BaseModel):
    blueprint: UIBlueprint
    model: str | None = None

@router.post("/ui")
async def generate_ui(req: GenerateRequest):
    """SSE stream: phase2 → phase3 → completed(with CodeArtifact)."""
```

**修改 `main.py`**:追加 `from app.api.generate import router as generate_router; app.include_router(generate_router)`。

### 测试 `tests/test_ui_pipeline.py`

用 mock LLM(同 test_repair.py 思路,但把 generate_with_repair 的调用链 mock 掉或提供假 LLMClient),覆盖:
1. Phase2 第一次返回合法 3 变体 → `Phase2Output.variants` 长度 3
2. Phase3 消费 variants[0] → 返回合法 CodeArtifact
3. Orchestrator.run yields 至少 3 个事件:phase2 / phase3 / completed
4. Phase2 返回 2 变体(非法,违反 min_length=3)→ 触发 repair

---

## 任务 #9:L6 打包交付器

### 文件:`backend/app/delivery/__init__.py`(空)

### 文件:`backend/app/delivery/packager.py`

```python
from __future__ import annotations
import io
import zipfile
from pathlib import Path
from app.core.stability.contracts import CodeArtifact

def package_as_zip(artifact: CodeArtifact, *, project_name: str = "generated-app") -> bytes:
    """Pack CodeArtifact into a ready-to-run Vite+React project zip.

    Ensures these files exist (auto-inject if missing from artifact):
    - README.md: 简短运行说明(pnpm i && pnpm dev)
    - .env.example: 含 SANDBOX_API_BASE / RUIDONG_API_KEY 占位
    - .gitignore: node_modules / dist / .env / .env.local

    File paths in artifact.files are relative to project root.
    """

def package_to_disk(artifact: CodeArtifact, dest_dir: Path, *, project_name: str = "generated-app") -> Path:
    """Write the zip to disk under dest_dir/{project_name}.zip. Returns the path."""
```

### 文件:`backend/app/api/delivery.py`

```python
from __future__ import annotations
from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.core.stability.contracts import CodeArtifact
from app.delivery.packager import package_as_zip

router = APIRouter(prefix="/api/delivery", tags=["delivery"])

class DeliveryRequest(BaseModel):
    artifact: CodeArtifact
    project_name: str = "generated-app"

@router.post("/download")
async def download(req: DeliveryRequest) -> StreamingResponse:
    """Return application/zip containing full project."""
```

**修改 main.py**:追加 include_router。

### 测试 `tests/test_packager.py`

- 输入含 `src/App.tsx` 的 CodeArtifact → zip 解压后文件存在、内容正确
- 未含 `.env.example` / `.gitignore` / `README.md` → 打包后自动注入
- `dependencies` 被写进 `package.json`(如果 artifact.files 里没 `package.json`,要自动生成一个合并 deps 的最小版)
- zip bytes 以 `PK\x03\x04` 开头

---

## 任务 #11:Parity 测试套件(替换占位)

### 文件:`tests/test_provider_parity.py`(覆盖现有占位)

目标:把"模型漂移抑制"转成可度量指标。用 mock 模拟 3 个不同模型(`light` / `mid` / `heavy`),对固定 SOP 跑整条管线,断言产出差异 ≤ 阈值。

```python
from __future__ import annotations
import json
import pytest
from typing import Any
from app.core.llm.client import ChatMessage
from app.core.stability.contracts import RequirementSpec, UIBlueprint
from app.core.orchestrator.meta_agent import MetaAgent
# Use Fake LLM pattern (see existing test_repair.py / test_meta_agent.py for style).

# Fixed SOP samples
SOP_SAMPLES = [
    "做一个公司内部的订单管理 SaaS,有仪表盘和订单列表",
    "我要一个博客平台,首页 + 文章列表 + 详情页",
    "HR 系统,员工列表 + 入职表单 + 请假审批",
]

# Canned LLM responses per model × per SOP (make them slightly different to simulate drift).
# Structure: {model_id: {sop_index: [response_sequence]}}
CANNED_RESPONSES: dict[str, dict[int, list[str]]] = { ... }

class FakeLLM:
    """Deterministic mock LLM for parity testing. Returns canned responses."""
    def __init__(self, model_responses: dict[str, list[str]]) -> None: ...
    async def chat(self, model: str, messages: list[ChatMessage], **kwargs) -> ChatMessage: ...

def _compute_drift_score(spec_a: RequirementSpec, spec_b: RequirementSpec) -> float:
    """Return 0-1, 0=identical, 1=totally different.
    Dimensions:
    - page count diff (weighted 0.4)
    - component-type set diff (0.3)
    - field name set diff (0.3)"""

@pytest.mark.parametrize("sop_index", range(len(SOP_SAMPLES)))
@pytest.mark.asyncio
async def test_model_drift_is_bounded(sop_index: int) -> None:
    """For each SOP, MetaAgent output across 3 mock models must differ ≤ 15%."""
    # Run MetaAgent.build_spec for each model with same SOP
    # Compute pairwise drift scores; assert max ≤ 0.15
```

**要点:**
- Canned 响应要**接近但不完全相同**(模拟真实模型行为),drift 必须 ≤ 0.15 才通过
- 至少 3 个模型 × 3 个 SOP = 9 次管线跑
- `_compute_drift_score` 的实现要简单、确定,测试就用它
- 不要 skip 这个测试 — 这是 Sprint 1 的 CI 门槛

---

## 文件清单(新增 11 + 修改 2)

**新增:**
- `backend/app/core/pipelines/__init__.py`
- `backend/app/core/pipelines/ui/__init__.py`
- `backend/app/core/pipelines/ui/prompts.py`
- `backend/app/core/pipelines/ui/phase2_prototype.py`
- `backend/app/core/pipelines/ui/phase3_production.py`
- `backend/app/core/pipelines/ui/orchestrator.py`
- `backend/app/delivery/__init__.py`
- `backend/app/delivery/packager.py`
- `backend/app/api/generate.py`
- `backend/app/api/delivery.py`
- `tests/test_ui_pipeline.py`
- `tests/test_packager.py`

**覆盖/修改:**
- `tests/test_provider_parity.py`(完全重写,删 skip)
- `backend/app/main.py`(追加 2 个 include_router)

## 实现约束

- 所有 Python 文件 `from __future__ import annotations`
- Pydantic v2 风格 ConfigDict
- 单行 docstring
- 不引入新依赖(zipfile 是标准库,够用)
- 测试用 monkeypatch.setattr 替换 asyncio.sleep / random.uniform,别让测试真的睡
- 不要重新发明 LLMClient mock — 复用 Batch-2 建立的 FakeLLM 模式

## 完成后

1. `find backend/app tests -type f -newer pyproject.toml | grep -v __pycache__ | sort` 列出文件
2. 150 字交付总结:新文件数、最关键决策、1 个潜在问题
3. **不跑测试/服务**
