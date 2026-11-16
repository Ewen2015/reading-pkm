"""Shared palette selection helpers for rednote-post."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
PALETTES = SKILL / "assets" / "palettes.json"
PALETTE_CSS = SKILL / "assets" / "palettes"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读不了 {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def palette_config() -> dict:
    cfg = read_json(PALETTES)
    if not isinstance(cfg.get("palettes"), dict):
        raise ValueError(f"{PALETTES} 缺少 palettes")
    return cfg


def parse_stamp(value: object) -> datetime | None:
    match = re.search(
        r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?",
        str(value or ""),
    )
    if not match:
        return None
    day, clock = match.group(1), match.group(2) or "00:00"
    try:
        return datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def manifest_stamp(manifest: dict, manifest_path: Path) -> datetime:
    """Order a post by its publication schedule, then read date, then mtime."""
    publish = manifest.get("publish") if isinstance(manifest.get("publish"), dict) else {}
    for value in (
        publish.get("scheduled_for"),
        publish.get("published_at"),
        publish.get("verified_at"),
    ):
        stamp = parse_stamp(value)
        if stamp:
            return stamp
    source = manifest.get("source_verification")
    if isinstance(source, dict):
        stamp = parse_stamp(source.get("read_at"))
        if stamp:
            return stamp
    try:
        return datetime.fromtimestamp(manifest_path.stat().st_mtime)
    except OSError:
        return datetime.min


def _post_title(post: Path, manifest: dict) -> str:
    title = ""
    title_file = post / "title.txt"
    if title_file.is_file():
        try:
            title = title_file.read_text(encoding="utf-8").strip()
        except OSError:
            title = ""
    if title:
        return title
    publish = manifest.get("publish")
    if isinstance(publish, dict) and publish.get("title"):
        return str(publish["title"]).strip()
    return post.name


def most_recent_other_palette(post: Path) -> dict | None:
    """Return the latest sibling post that already has a palette.

    The current post is excluded. Posts under the optional sibling ``published``
    directory are included. Deleted posts are ignored.
    """
    post = post.expanduser().resolve()
    roots = [post.parent]
    published = post.parent.parent / "published"
    if published.is_dir():
        roots.append(published)

    found: list[dict] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            siblings = sorted(root.iterdir())
        except OSError:
            continue
        for sibling in siblings:
            if sibling == post or sibling in seen:
                continue
            seen.add(sibling)
            manifest_path = sibling / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = read_json(manifest_path)
            except ValueError:
                continue
            palette = manifest.get("palette")
            if not isinstance(palette, dict):
                continue
            palette_id = str(palette.get("id") or "").strip()
            if not palette_id:
                continue
            publish = manifest.get("publish") if isinstance(manifest.get("publish"), dict) else {}
            if str(publish.get("status") or "").strip() == "已删除":
                continue
            found.append(
                {
                    "id": palette_id,
                    "name": str(palette.get("name") or palette_id),
                    "post": str(sibling),
                    "name_label": _post_title(sibling, manifest),
                    "scheduled_for": str(publish.get("scheduled_for") or ""),
                    "stamp": manifest_stamp(manifest, manifest_path),
                }
            )

    if not found:
        return None
    found.sort(key=lambda item: (item["stamp"], item["post"]), reverse=True)
    return found[0]
