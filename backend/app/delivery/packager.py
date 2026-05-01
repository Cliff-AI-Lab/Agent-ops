from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from app.core.stability.contracts import CodeArtifact
from app.core.trace.bus import emit

DEFAULT_README = """# Generated App

## Run

```bash
pnpm i
pnpm dev
```
"""

DEFAULT_ENV_EXAMPLE = """SANDBOX_API_BASE=
RUIDONG_API_KEY=
"""

DEFAULT_GITIGNORE = """node_modules
dist
.env
.env.local
"""


def package_as_zip(artifact: CodeArtifact, *, project_name: str = "generated-app") -> bytes:
    """Pack CodeArtifact into a ready-to-run Vite+React project zip."""
    root_name = _normalize_project_name(project_name)
    files = { _normalize_artifact_path(entry.path): entry.content for entry in artifact.files }
    files["package.json"] = _render_package_json(files.get("package.json"), artifact, project_name=root_name)
    files.setdefault("README.md", DEFAULT_README)
    files.setdefault(".env.example", DEFAULT_ENV_EXAMPLE)
    files.setdefault(".gitignore", DEFAULT_GITIGNORE)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in sorted(files.items()):
            archive.writestr(f"{root_name}/{relative_path}", content)
    payload = buffer.getvalue()
    emit("L6", "Packager", "zipped",
         f"✓ zip {len(payload):,} bytes · {len(files)} entries · project={root_name}",
         data={"bytes": len(payload), "entries": len(files)})
    return payload


def package_to_disk(artifact: CodeArtifact, dest_dir: Path, *, project_name: str = "generated-app") -> Path:
    """Write the zip to disk under dest_dir/{project_name}.zip. Returns the path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / f"{_normalize_project_name(project_name)}.zip"
    zip_path.write_bytes(package_as_zip(artifact, project_name=project_name))
    return zip_path


def _normalize_project_name(project_name: str) -> str:
    cleaned = project_name.strip().replace("\\", "-").replace("/", "-")
    return cleaned or "generated-app"


def _normalize_artifact_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"Artifact file path must be relative: {path}")
    cleaned = normalized.as_posix()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned:
        raise ValueError("Artifact file path cannot be empty.")
    return cleaned


def _render_package_json(existing_content: str | None, artifact: CodeArtifact, *, project_name: str) -> str:
    dependencies = dict(sorted(artifact.dependencies.items()))
    if existing_content:
        try:
            package_json = json.loads(existing_content)
        except json.JSONDecodeError:
            package_json = _minimal_package_json(project_name)
    else:
        package_json = _minimal_package_json(project_name)

    existing_dependencies = package_json.get("dependencies", {})
    if not isinstance(existing_dependencies, dict):
        existing_dependencies = {}
    package_json["dependencies"] = {**existing_dependencies, **dependencies}
    return json.dumps(package_json, ensure_ascii=False, indent=2) + "\n"


def _minimal_package_json(project_name: str) -> dict[str, object]:
    return {
        "name": project_name,
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "tsc -b && vite build",
            "preview": "vite preview",
        },
        "dependencies": {},
        "devDependencies": {
            "@vitejs/plugin-react": "^4.3.4",
            "typescript": "^5.6.3",
            "vite": "^5.4.11",
        },
    }
