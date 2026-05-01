from __future__ import annotations

import io
import json
import zipfile

from app.core.stability.contracts import CodeArtifact
from app.delivery.packager import package_as_zip


def _artifact() -> CodeArtifact:
    return CodeArtifact.model_validate(
        {
            "files": [
                {
                    "path": "src/App.tsx",
                    "content": "export default function App() { return <main>Orders</main>; }",
                }
            ],
            "dependencies": {
                "react": "^18.3.1",
                "react-dom": "^18.3.1",
                "react-router-dom": "^6.28.0",
            },
            "entrypoint": "src/App.tsx",
        }
    )


def test_package_as_zip_round_trips_project_files() -> None:
    payload = package_as_zip(_artifact(), project_name="demo-app")

    assert payload.startswith(b"PK\x03\x04")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "demo-app/src/App.tsx" in archive.namelist()
        content = archive.read("demo-app/src/App.tsx").decode("utf-8")

    assert "Orders" in content


def test_package_as_zip_injects_project_defaults_and_dependencies() -> None:
    payload = package_as_zip(_artifact(), project_name="delivery-check")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        package_json = json.loads(archive.read("delivery-check/package.json").decode("utf-8"))
        env_example = archive.read("delivery-check/.env.example").decode("utf-8")
        gitignore = archive.read("delivery-check/.gitignore").decode("utf-8")
        readme = archive.read("delivery-check/README.md").decode("utf-8")

    assert "delivery-check/package.json" in names
    assert "delivery-check/.env.example" in names
    assert "delivery-check/.gitignore" in names
    assert "delivery-check/README.md" in names
    assert package_json["dependencies"]["react"] == "^18.3.1"
    assert package_json["dependencies"]["react-router-dom"] == "^6.28.0"
    assert "SANDBOX_API_BASE=" in env_example
    assert "RUIDONG_API_KEY=" in env_example
    assert "node_modules" in gitignore
    assert "pnpm dev" in readme
