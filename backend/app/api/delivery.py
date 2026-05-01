from __future__ import annotations

import io

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.core.stability.contracts import CodeArtifact
from app.delivery.packager import package_as_zip

router = APIRouter(prefix="/api/delivery", tags=["delivery"])


class DeliveryRequest(BaseModel):
    """Request payload for zip delivery."""

    artifact: CodeArtifact
    project_name: str = "generated-app"

    model_config = ConfigDict(extra="forbid")


@router.post("/download")
async def download(req: DeliveryRequest) -> StreamingResponse:
    """Return application/zip containing full project."""
    payload = package_as_zip(req.artifact, project_name=req.project_name)
    headers = {"Content-Disposition": f'attachment; filename="{req.project_name}.zip"'}
    return StreamingResponse(io.BytesIO(payload), media_type="application/zip", headers=headers)
