#!/usr/bin/env python3
"""Apply a book-theme palette to a post directory.

Usage:
    python3 apply_palette.py --list
    python3 apply_palette.py <post-dir> <palette-id> --reason "为什么适合这本书"
    python3 apply_palette.py <post-dir> auto \
      --candidates clay-archive,cobalt-editorial --reason "主题允许的两三套方向"
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

from palette_utils import PALETTE_CSS, palette_config, read_json, most_recent_other_palette


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
    try:
        cfg = palette_config()
    except ValueError as exc:
        sys.exit(str(exc))
    ap = argparse.ArgumentParser()
    ap.add_argument("post", nargs="?", type=Path)
    ap.add_argument(
        "palette",
        nargs="?",
        help="palette-id；或 auto，从 --candidates 里随机选且避开上一篇",
    )
    ap.add_argument(
        "--candidates",
        default="",
        help="auto 模式的主题候选色板 id，逗号分隔；至少两套",
    )
    ap.add_argument("--seed", help="auto 模式的固定随机种子，仅用于复核")
    ap.add_argument("--reason", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for key, item in cfg["palettes"].items():
            mark = " (fallback)" if key == cfg.get("default") else ""
            family = item.get("family") or item.get("temperament") or ""
            print(
                f"{key}{mark}\t{item['name']}\t{family}\t"
                f"{'、'.join(item['use_for'][:3])}"
            )
        return 0

    if not args.post or not args.palette:
        ap.error("需要 <post-dir> 和 <palette-id>；先跑 --list 看选项")
    reason = args.reason.strip()
    if not reason:
        sys.exit("缺少 --reason：写清这本书的主题为什么适合这套色板")

    post = args.post.expanduser().resolve()
    manifest_path = post / "manifest.json"
    build = post / "build"
    if not manifest_path.is_file() or not build.is_dir():
        sys.exit(f"{post} 不是完整 post 目录（需要 manifest.json 和 build/）")

    recent = most_recent_other_palette(post)
    candidates: list[str] = []
    if args.palette == "auto":
        candidates = list(dict.fromkeys(
            value.strip()
            for value in args.candidates.split(",")
            if value.strip()
        ))
        if len(candidates) < 2:
            sys.exit(
                "auto 模式需要 --candidates，至少给出两套主题上可接受的色板；"
                "例如 --candidates clay-archive,cobalt-editorial"
            )
        unknown = [value for value in candidates if value not in cfg["palettes"]]
        if unknown:
            sys.exit(f"未知候选色板：{', '.join(unknown)}")
        allowed = [
            value
            for value in candidates
            if not recent or value != recent["id"]
        ]
        if not allowed:
            sys.exit(
                f"候选里只有上一篇用过的 {recent['id']}；至少再加一套不同色板。"
                f"上一篇：{recent['name_label']}（{recent['id']}）"
            )
        chooser = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
        palette_id = chooser.choice(allowed)
        selection = {
            "mode": "random",
            "candidates": candidates,
            "excluded": [recent["id"]] if recent else [],
            "anti_repeat": True,
        }
    else:
        palette_id = args.palette
        if palette_id not in cfg["palettes"]:
            sys.exit(f"未知色板 {palette_id}；可选：{', '.join(cfg['palettes'])}")
        if recent and palette_id == recent["id"]:
            sys.exit(
                f"连续两篇不能用同一个色板：上一篇已经是 {palette_id}。"
                f"上一篇：{recent['name_label']}（{recent['scheduled_for'] or '无排期'}）；"
                "改用 auto + --candidates，或手选另一套主题合适的色板。"
            )
        selection = {
            "mode": "manual",
            "excluded": [recent["id"]] if recent else [],
            "anti_repeat": True,
        }

    selected = cfg["palettes"][palette_id]
    source = PALETTE_CSS / f"{palette_id}.css"
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
        "id": palette_id,
        "name": selected["name"],
        "family": selected.get("family") or "",
        "reason": reason,
        "selection": {
            **selection,
            "previous": (
                {
                    "id": recent["id"],
                    "post": recent["name_label"],
                    "scheduled_for": recent["scheduled_for"],
                }
                if recent
                else None
            ),
        },
    }
    manifest["background"] = selected["colors"]["paper"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"色板：{palette_id} / {selected['name']}")
    if recent:
        print(f"上一篇：{recent['name_label']} / {recent['id']}（已排除重复）")
    print(f"理由：{reason}")
    print(f"CSS：{build / 'palette.css'}")
    print(f"插入 palette.css 的页面：{', '.join(changed) if changed else '已存在，未重复插入'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
