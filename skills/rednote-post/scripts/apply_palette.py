#!/usr/bin/env python3
"""Apply a book-theme palette to a post directory.

Usage:
    python3 apply_palette.py --list
    python3 apply_palette.py <post-dir> <palette-id> --reason "为什么适合这本书"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
PALETTES = SKILL / "assets" / "palettes.json"
PALETTE_CSS = SKILL / "assets" / "palettes"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"读不了 {path}: {exc}")


def palette_config() -> dict:
    cfg = read_json(PALETTES)
    if not isinstance(cfg.get("palettes"), dict):
        sys.exit(f"{PALETTES} 缺少 palettes")
    return cfg


def add_stylesheet(html: str, href: str = "palette.css") -> str:
    # Re-insert instead of trusting an existing link: if a hand edit put it before
    # card.css, the base palette would win and the selected theme would silently vanish.
    html = re.sub(
        r'\s*<link[^>]+href=["\']palette\.css["\'][^>]*>',
        "",
        html,
        flags=re.I,
    )
    link = f'<link rel="stylesheet" href="{href}">'
    match = re.search(r'<link[^>]+href=["\']card\.css["\'][^>]*>', html, re.I)
    if match:
        return html[:match.end()] + "\n" + link + html[match.end():]
    if re.search(r"</head>", html, re.I):
        return re.sub(r"</head>", f"  {link}\n</head>", html, count=1, flags=re.I)
    return html


def main() -> int:
    cfg = palette_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("post", nargs="?", type=Path)
    ap.add_argument("palette", nargs="?")
    ap.add_argument("--reason", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for key, item in cfg["palettes"].items():
            mark = " (default)" if key == cfg.get("default") else ""
            print(f"{key}{mark}\t{item['name']}\t{'、'.join(item['use_for'][:3])}")
        return 0

    if not args.post or not args.palette:
        ap.error("需要 <post-dir> 和 <palette-id>；先跑 --list 看选项")
    if args.palette not in cfg["palettes"]:
        sys.exit(f"未知色板 {args.palette}；可选：{', '.join(cfg['palettes'])}")
    reason = args.reason.strip()
    if not reason:
        sys.exit("缺少 --reason：写清这本书的主题为什么适合这套色板")

    post = args.post.expanduser().resolve()
    manifest_path = post / "manifest.json"
    build = post / "build"
    if not manifest_path.is_file() or not build.is_dir():
        sys.exit(f"{post} 不是完整 post 目录（需要 manifest.json 和 build/）")

    selected = cfg["palettes"][args.palette]
    source = PALETTE_CSS / f"{args.palette}.css"
    if not source.is_file():
        sys.exit(f"色板 CSS 不存在：{source}")

    shutil.copy2(source, build / "palette.css")
    changed = []
    for html_path in sorted(build.glob("*.html")):
        old = html_path.read_text(encoding="utf-8")
        new = add_stylesheet(old)
        if new != old:
            html_path.write_text(new, encoding="utf-8")
            changed.append(html_path.name)

    manifest = read_json(manifest_path)
    manifest["palette"] = {
        "id": args.palette,
        "name": selected["name"],
        "reason": reason,
    }
    manifest["background"] = selected["colors"]["paper"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"色板：{args.palette} / {selected['name']}")
    print(f"理由：{reason}")
    print(f"CSS：{build / 'palette.css'}")
    print(f"插入 palette.css 的页面：{', '.join(changed) if changed else '已存在，未重复插入'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
