"""Enable HF Serverless Inference Providers on our four Stage 2 repos.

HuggingFace stopped auto-deploying small/private models on their free
serverless tier. The opt-in signal is ``inference: true`` in the
model card's YAML front-matter. This script downloads each repo's
README.md, patches the front-matter, and uploads the change.

Run once per repo (idempotent — re-running is a no-op when the line
is already there).

Usage:
    python scripts/push_to_hf/enable_inference.py

Reads the HF token from huggingface-cli's cache (run
``huggingface-cli login`` first).

Note: provenance + contents are custom-code repos and may stay
unavailable on the serverless tier no matter what (HF refuses
custom-code on free serverless). The line still gets set for
completeness — when HF adds custom-code support to serverless, those
repos will light up automatically.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# (repo_id, pipeline_tag, language list)
REPOS: list[tuple[str, str, list[str]]] = [
    ("alexgoldberg/hebrew-manuscript-joint-ner-v2",
        "token-classification", ["he"]),
    ("alexgoldberg/hebrew-manuscript-provenance-ner-v2",
        "token-classification", ["he"]),
    ("alexgoldberg/hebrew-manuscript-contents-ner",
        "token-classification", ["he"]),
    ("alexgoldberg/hebrew-manuscript-genre-classifier",
        "text-classification", ["he", "en"]),
]


def main() -> int:
    from huggingface_hub import HfApi, hf_hub_download  # noqa: PLC0415
    api = HfApi()

    for repo, pipeline_tag, langs in REPOS:
        try:
            new_readme = _patched_readme(
                api, hf_hub_download, repo=repo,
                pipeline_tag=pipeline_tag, languages=langs,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("✗ %s: could not patch README — %s", repo, exc)
            continue

        # Upload the file. HF Hub accepts a string body via a temp
        # file; we use the simpler ``upload_file`` with bytes.
        api.upload_file(
            path_or_fileobj=new_readme.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=repo,
            repo_type="model",
            commit_message="enable HF Serverless Inference (inference: true)",
        )
        log.info("✓ %s: README.md updated", repo)

    log.info("Done. Wait ~30s, then refresh each model page to see "
              "the 'Inference API' widget on the right.")
    return 0


def _patched_readme(
    api: Any, downloader: Any, *,
    repo: str, pipeline_tag: str, languages: list[str],
) -> str:
    """Return the README with inference + pipeline + language set."""
    try:
        local = downloader(repo_id=repo, filename="README.md", repo_type="model")
        with open(local, encoding="utf-8") as fp:
            text = fp.read()
    except Exception:  # noqa: BLE001
        text = ""

    front_matter, body = _split_front_matter(text)

    # Apply changes to the front-matter dict — preserve everything else.
    front_matter["pipeline_tag"] = pipeline_tag
    front_matter["inference"]    = True
    if "language" not in front_matter:
        front_matter["language"] = languages

    return _join_front_matter(front_matter, body)


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the leading ``---\\n…\\n---\\n`` block off *text*."""
    import yaml  # noqa: PLC0415

    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text
    rest = stripped[3:]
    end = rest.find("\n---")
    if end < 0:
        return {}, text
    front_yaml = rest[:end]
    body = rest[end + len("\n---"):].lstrip("\n")
    try:
        data = yaml.safe_load(front_yaml) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body


def _join_front_matter(front: dict[str, Any], body: str) -> str:
    import yaml  # noqa: PLC0415

    if not front:
        return body
    rendered = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{rendered}\n---\n\n{body}"


if __name__ == "__main__":
    sys.exit(main())
