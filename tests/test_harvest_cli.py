"""Tests for harvest CLI - injects a fake fetcher to avoid network in CI."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.cli.harvest import harvest, main


SAMPLE_CSV = """act,prompt
Linux Terminal,I want you to act as a Linux terminal. I will type commands and you will reply with what the terminal should show.
English Translator,I want you to act as an English translator and improver. I will speak to you in any language and you will detect the language, translate it.
"""


def _fake_fetcher(_url: str) -> str:
    return SAMPLE_CSV


def test_harvest_writes_prompt_yaml(tmp_path: Path):
    summary = harvest(
        "awesome-chatgpt-prompts",
        out_dir=tmp_path,
        fetcher=_fake_fetcher,
    )
    assert summary["fetched_count"] == 2
    assert summary["written_count"] == 2

    written = list(tmp_path.rglob("*.yaml"))
    assert len(written) == 2

    one = yaml.safe_load(written[0].read_text(encoding="utf-8"))
    assert one["type"] == "prompt"
    assert one["asset_id"].startswith("prompt.role_play.")
    assert "{{ user_message }}" in one["template"]
    assert one["provenance"]["built_by"] == "github_harvest"
    assert one["provenance"]["license"] == "CC0-1.0"


def test_harvest_dry_run(tmp_path: Path):
    summary = harvest(
        "awesome-chatgpt-prompts",
        out_dir=tmp_path,
        fetcher=_fake_fetcher,
        dry_run=True,
    )
    assert summary["dry_run"] is True
    assert summary["fetched_count"] == 2
    written = list(tmp_path.rglob("*.yaml"))
    assert len(written) == 0


def test_harvest_limit(tmp_path: Path):
    summary = harvest(
        "awesome-chatgpt-prompts",
        out_dir=tmp_path,
        fetcher=_fake_fetcher,
        limit=1,
    )
    assert summary["written_count"] == 1


def test_harvest_subcategory_override(tmp_path: Path):
    summary = harvest(
        "awesome-chatgpt-prompts",
        out_dir=tmp_path,
        fetcher=_fake_fetcher,
        sub="custom",
    )
    assert summary["written_count"] == 2
    written = list(tmp_path.rglob("*.yaml"))
    one = yaml.safe_load(written[0].read_text(encoding="utf-8"))
    assert one["asset_id"].startswith("prompt.custom.")


def test_harvest_skips_short_prompts(tmp_path: Path):
    short_csv = """act,prompt
Too Short,hi
Long Enough,I want you to act as a thing that does many things and acts very well.
"""

    def _fetch(_):
        return short_csv

    summary = harvest("awesome-chatgpt-prompts", out_dir=tmp_path, fetcher=_fetch)
    assert summary["fetched_count"] == 1  # short one was filtered
    assert summary["written_count"] == 1


def test_harvest_dedup_within_run(tmp_path: Path):
    dup_csv = """act,prompt
Linux Terminal,I want you to act as a Linux terminal. I will type commands.
Linux Terminal,I want you to act as a Linux terminal. Different body but same act.
"""

    def _fetch(_):
        return dup_csv

    summary = harvest("awesome-chatgpt-prompts", out_dir=tmp_path, fetcher=_fetch)
    assert summary["written_count"] == 1
    assert summary["skipped_count"] == 1
    assert "duplicate" in summary["skipped_reasons"][0]


def test_harvest_yields_loadable_prompts(tmp_path: Path):
    """End-to-end: harvested YAML round-trips through PromptDef Pydantic model."""
    from app.registry.prompt_loader import PromptDef

    harvest(
        "awesome-chatgpt-prompts",
        out_dir=tmp_path,
        fetcher=_fake_fetcher,
    )
    written = list(tmp_path.rglob("*.yaml"))
    for path in written:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        prompt = PromptDef.model_validate(data)
        assert prompt.asset_id.startswith("prompt.")


def test_main_unknown_source():
    """argparse choices reject -> SystemExit(2)."""
    with pytest.raises(SystemExit) as exc:
        main(["unknown-source"])
    assert exc.value.code == 2
