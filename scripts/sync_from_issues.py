#!/usr/bin/env python3
"""
Синхронизация каталога technologies/*.md из issues репозитория mrromast/-.
Запуск: python scripts/sync_from_issues.py
Переменные: GITHUB_TOKEN (опционально, для лимитов API), SOURCE_REPO (по умолчанию mrromast/-)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE_OWNER = "mrromast"
SOURCE_REPO = "-"
SOURCE_REPO_FULL = os.environ.get("SOURCE_REPO", f"{SOURCE_OWNER}/{SOURCE_REPO}")
ROOT = Path(__file__).resolve().parents[1]
TECH_DIR = ROOT / "technologies"
README_PATH = ROOT / "README.md"

SYNC_START = "<!-- sync-meta-start -->"
SYNC_END = "<!-- sync-meta-end -->"


def api_request(url: str, token: str | None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "chinese-tech-transfer-sync",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {e.code} for {url}: {body}") from e


def fetch_all_issues(owner: str, repo: str, token: str | None) -> list[dict]:
    issues: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode(
            {"state": "all", "per_page": "100", "page": str(page), "direction": "asc", "sort": "number"}
        )
        url = f"https://api.github.com/repos/{owner}/{repo}/issues?{q}"
        batch = api_request(url, token)
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            if item.get("pull_request"):
                continue
            issues.append(item)
        if len(batch) < 100:
            break
        page += 1
    issues.sort(key=lambda x: x["number"])
    return issues


def state_ru(state: str) -> str:
    return "Завершено" if state == "closed" else "Открыто"


def slugify_title(title: str, max_len: int = 55) -> str:
    t = title.strip()
    if t.startswith("🆕"):
        t = t[len("🆕") :].strip()
    t = re.sub(r'[«»"\'/\\|:*?<>]', "", t)
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    if len(t) > max_len:
        t = t[:max_len].rstrip("_")
    return t or "issue"


def filename_for_issue(number: int, title: str) -> str:
    return f"{number:02d}_{slugify_title(title)}.md"


def format_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d")


def render_issue_md(issue: dict) -> str:
    number = issue["number"]
    title = issue["title"] or f"Issue #{number}"
    labels = [lb["name"] for lb in issue.get("labels") or []]
    categories = ", ".join(labels) if labels else "—"
    updated = format_date(issue["updated_at"])
    status = state_ru(issue["state"])
    body = (issue.get("body") or "").strip()
    source = f"https://github.com/{SOURCE_OWNER}/{SOURCE_REPO}/issues/{number}"

    lines = [
        f"# {title}",
        "",
        f"- **Номер:** #{number}",
        f"- **Статус:** {status}",
        f"- **Категории:** {categories}",
        f"- **Источник:** [{SOURCE_OWNER}/{SOURCE_REPO}#{number}]({source})",
        f"- **Дата:** {updated}",
        "",
        "---",
        "",
    ]
    if body:
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def render_catalog_readme(entries: list[dict]) -> str:
    lines = [
        "# Каталог технологий",
        "",
        "_Каталог обновляется автоматически из [issues @mrromast](https://github.com/mrromast/-/issues)._",
        "",
        "| № | Технология | Статус | Файл |",
        "|---:|---|---|---|",
    ]
    for e in entries:
        rel = f"technologies/{e['filename']}"
        lines.append(
            f"| {e['number']} | {e['title']} | {e['status']} | [{rel}]({rel}) |"
        )
    lines.append("")
    return "\n".join(lines)


def update_main_readme(count: int, synced_at: str) -> None:
    if not README_PATH.exists():
        return
    text = README_PATH.read_text(encoding="utf-8")
    block = (
        f"{SYNC_START}\n"
        f"| **Последняя синхронизация** | {synced_at} (UTC) |\n"
        f"| **Технологий в каталоге** | {count} |\n"
        f"| **Источник данных** | Авто-синхронизация из [mrromast/- issues](https://github.com/mrromast/-/issues) |\n"
        f"{SYNC_END}"
    )
    pattern = re.compile(
        re.escape(SYNC_START) + r".*?" + re.escape(SYNC_END),
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        needle = "| **Исходные issues** |"
        if needle in text:
            text = text.replace(
                needle,
                f"{block}\n| **Исходные issues** |",
                1,
            )
        else:
            text += f"\n\n{block}\n"
    README_PATH.write_text(text, encoding="utf-8")


def cleanup_old_files(keep_names: set[str]) -> list[str]:
    removed = []
    for path in TECH_DIR.glob("*.md"):
        if path.name == "README.md":
            continue
        if path.name not in keep_names:
            path.unlink()
            removed.append(path.name)
    return removed


def main() -> int:
    owner, repo = SOURCE_REPO_FULL.split("/", 1)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    print(f"Fetching issues from {SOURCE_REPO_FULL}...")
    issues = fetch_all_issues(owner, repo, token)
    print(f"Found {len(issues)} issues")

    TECH_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    keep_names: set[str] = {"README.md"}

    for issue in issues:
        number = issue["number"]
        title = issue["title"] or f"Issue #{number}"
        fname = filename_for_issue(number, title)
        keep_names.add(fname)
        path = TECH_DIR / fname
        content = render_issue_md(issue)
        path.write_text(content, encoding="utf-8")
        entries.append(
            {
                "number": number,
                "title": title,
                "status": state_ru(issue["state"]),
                "filename": fname,
            }
        )
        print(f"  #{number} -> {fname}")

    removed = cleanup_old_files(keep_names)
    if removed:
        print(f"Removed obsolete files: {', '.join(removed)}")

    (TECH_DIR / "README.md").write_text(render_catalog_readme(entries), encoding="utf-8")

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    update_main_readme(len(entries), synced_at)

    print(f"Catalog index updated ({len(entries)} technologies)")
    print(f"Last sync: {synced_at} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
