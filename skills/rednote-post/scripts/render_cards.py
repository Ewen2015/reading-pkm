#!/usr/bin/env python3
"""Render 小红书 card HTML to images and verify the layout actually fits.

Usage:
    python3 render_cards.py <post-dir> [--dsf 1] [--no-jpg] [--min-font 20]

Reads <post-dir>/manifest.json:
    {
      "width": 1080, "height": 1440, "dsf": 2,
      "min_font": 20,            # hard failure below this, expressed as px @1080-wide
      "target_font": 24,         # warning below this, likewise
      "jpg_width": 1440, "background": "F3EEE5",
      "root": ".card",                                    # card root element
      "metadata_selectors": [".top", ".foot", ".rlab"],    # exempt from font floor
      "cards": [
        {"html": "build/cover.html", "out": "01-封面"},
        {"html": "build/wide.html", "out": "02-全图", "width": 1280, "height": 720}
      ]
    }

Font sizes are compared as a FRACTION OF CANVAS WIDTH, not in absolute px. The phone
shows the image at roughly screen width, so 24px in a 1080-wide card and 28px in a
1280-wide card look the same to the reader. `min_font` / `target_font` are given as
the px value that would have that meaning in a 1080-wide card; the renderer converts
per card. This is what catches a dense print-style layout that is technically fine on
a desktop screen and unreadable on a phone.

Writes <post-dir>/<out>.png (and .jpg) and exits non-zero if any card fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CHROME_CANDIDATES = [
    os.environ.get("CHROME"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome"),
    shutil.which("chromium"),
    shutil.which("chromium-browser"),
]

PROBE = r"""
<script>
addEventListener('load', () => {
  const report = (o) => document.body.setAttribute('data-report', JSON.stringify(o));
  const card = document.querySelector(__ROOT__);
  if (!card) return report({ error: '找不到卡片根元素 ' + __ROOT__ });
  const META = __META__;
  const cardBox = card.getBoundingClientRect();

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0.05;
  };
  const hasOwnText = (el) =>
    [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);
  const isMeta = (el) => META.some((s) => { try { return !!el.closest(s); } catch (e) { return false; } });

  // ---- content bottom vs footer top -------------------------------------
  let contentBottom = 0;
  for (const el of card.children) {
    if (getComputedStyle(el).position === 'absolute') continue;
    const b = el.getBoundingClientRect().bottom - cardBox.top;
    if (b > contentBottom) contentBottom = b;
  }
  const foot = card.querySelector('.foot');
  const footTop = foot ? foot.getBoundingClientRect().top - cardBox.top : null;

  // ---- font sizes in the content area -----------------------------------
  const small = [];
  let minFont = Infinity;
  for (const el of card.querySelectorAll('*')) {
    if (isMeta(el) || !hasOwnText(el) || !visible(el)) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < minFont) minFont = fs;
    if (fs < __TARGET__) {
      small.push({
        fs: Math.round(fs * 10) / 10,
        cls: (el.className || '').toString().slice(0, 40),
        text: (el.textContent || '').trim().slice(0, 24),
      });
    }
  }

  // ---- orphaned last lines ---------------------------------------------
  // Rect runs are clustered into visual lines by vertical overlap. Grouping by
  // rounded `top` instead splits a single line whenever an inline <b> sits a
  // fraction of a pixel off, inventing phantom lines and false orphans.
  const orphans = [];
  const blocks = '.rd, .td, .use, .it, .order p, .quote b, .quote i, .note, p, li';
  for (const el of card.querySelectorAll(blocks)) {
    if (!hasOwnText(el) || !visible(el)) continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = [...range.getClientRects()].filter((r) => r.width > 0.5);
    if (!rects.length) continue;

    const lines = [];
    for (const r of rects.map((r) => ({ top: r.top, bottom: r.bottom, left: r.left, right: r.right }))
                        .sort((a, b) => a.top - b.top)) {
      const hit = lines.find((ln) => {
        const overlap = Math.min(ln.bottom, r.bottom) - Math.max(ln.top, r.top);
        return overlap > 0.5 * Math.min(ln.bottom - ln.top, r.bottom - r.top);
      });
      if (hit) {
        hit.top = Math.min(hit.top, r.top);
        hit.bottom = Math.max(hit.bottom, r.bottom);
        hit.left = Math.min(hit.left, r.left);
        hit.right = Math.max(hit.right, r.right);
      } else lines.push({ ...r });
    }
    if (lines.length < 2) continue;

    const last = lines.reduce((a, b) => (b.top > a.top ? b : a));
    const inner = el.clientWidth
      - (parseFloat(getComputedStyle(el).paddingLeft) || 0)
      - (parseFloat(getComputedStyle(el).paddingRight) || 0);
    if (inner <= 0) continue;
    const fill = (last.right - last.left) / inner;
    if (fill < 0.4) {
      orphans.push({
        lines: lines.length,
        fill: Math.round(fill * 100) / 100,
        text: (el.textContent || '').trim().slice(0, 30),
      });
    }
  }

  report({
    canvas_width: card.clientWidth,
    overflow: Math.round(card.scrollHeight - card.clientHeight),
    content_bottom: Math.round(contentBottom),
    foot_top: footTop === null ? null : Math.round(footTop),
    footer_gap: footTop === null ? null : Math.round(footTop - contentBottom),
    min_font: minFont === Infinity ? null : Math.round(minFont * 10) / 10,
    small,
    orphans,
  });
});
</script>
"""


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("找不到 Chrome。用 CHROME=/path/to/chrome 指定。")


def run_chrome(chrome: str, args: list[str]) -> str:
    return subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", *args],
        capture_output=True, text=True, errors="replace",
    ).stdout


REFERENCE_WIDTH = 1080.0


def render_png(chrome: str, html: Path, out: Path, w: int, h: int, dsf: int, bg: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    run_chrome(chrome, [
        f"--force-device-scale-factor={dsf}",
        f"--default-background-color={bg}",
        "--virtual-time-budget=4000",
        "--window-size=%d,%d" % (w, h),
        f"--screenshot={out}",
        html.resolve().as_uri(),
    ])
    if not out.exists():
        raise RuntimeError(f"渲染失败：{html}")


def measure(chrome: str, html: Path, w: int, h: int, root: str,
            meta: list[str], target: float) -> dict:
    patched = (PROBE.replace("__ROOT__", json.dumps(root))
                    .replace("__META__", json.dumps(meta))
                    .replace("__TARGET__", str(target)))
    src = html.read_text(encoding="utf-8")
    # The probe copy must live next to the original: a temp-dir copy would
    # break every relative href (card.css, images) and silently measure an
    # unstyled page — 16px everything, which reads as "the design is broken".
    tmp = html.with_name(f".probe-{html.stem}.html")
    tmp.write_text(
        src.replace("</body>", patched + "</body>") if "</body>" in src else src + patched,
        encoding="utf-8",
    )
    try:
        dom = run_chrome(chrome, [
            "--virtual-time-budget=4000", "--window-size=%d,%d" % (w, h),
            "--dump-dom", tmp.as_uri(),
        ])
    finally:
        tmp.unlink(missing_ok=True)
    m = re.search(r'data-report="(.*?)"', dom, re.S)
    if not m:
        return {"error": "页面没有产出 data-report（探针脚本未执行）"}
    raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">"))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"无法解析 report: {raw[:160]}"}


def equiv1080(px: float, canvas_w: float) -> float:
    """Re-express a font size as what it would look like in a 1080-wide card."""
    if not canvas_w:
        return px
    return px * REFERENCE_WIDTH / canvas_w


def judge(rep: dict, min_font: float) -> tuple[list[str], list[str]]:
    if "error" in rep:
        return [rep["error"]], []
    fails, warns = [], []
    cw = rep.get("canvas_width") or REFERENCE_WIDTH

    if rep.get("overflow", 0) > 0:
        fails.append(f"内容溢出 {rep['overflow']}px（超出卡片高度）")

    gap = rep.get("footer_gap")
    if gap is not None:
        if gap < 0:
            fails.append(f"内容压到页脚，越界 {-gap}px")
        elif gap < 40:
            warns.append(f"内容距页脚仅 {gap}px，偏挤")
        elif gap > 320:
            warns.append(f"内容距页脚 {gap}px（>320），版面偏空——补一条内容或加大区块间距，别留三分之一空白")

    mf = rep.get("min_font")
    if mf is not None:
        eff = equiv1080(mf, cw)
        if eff < min_font:
            fails.append(
                f"正文区最小字号 {mf}px（画布宽 {cw} → 相当于 1080 宽下的 {eff:.1f}px）"
                f" 低于下限 {min_font:g}px"
                "；若整版都偏小，先确认卡片的样式表真的生效了"
            )
    for s in rep.get("small", [])[:6]:
        warns.append(f"低于目标字号：{s['fs']}px（≈{equiv1080(s['fs'], cw):.1f}px@1080）"
                     f" {s['cls'] or '(无类名)'}「{s['text']}」")

    for o in rep.get("orphans", []):
        warns.append(f"孤字换行（{o['lines']} 行，末行仅占 {int(o['fill'] * 100)}%）：「{o['text']}」")
    return fails, warns


def to_jpg(png: Path, jpg: Path, width: int, quality: int = 93) -> None:
    try:
        from PIL import Image
    except ImportError:
        print("    （未装 Pillow，跳过 JPG 导出）")
        return
    im = Image.open(png).convert("RGB")
    if im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(jpg, "JPEG", quality=quality, subsampling=0, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染小红书卡片图并校验版式")
    ap.add_argument("post_dir", type=Path)
    ap.add_argument("--dsf", type=int, default=None, help="覆盖 manifest 的缩放（试版用 1）")
    ap.add_argument("--min-font", type=float, default=None, help="硬失败字号下限")
    ap.add_argument("--no-jpg", action="store_true")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON 报告")
    args = ap.parse_args()

    root = args.post_dir.resolve()
    mf = root / "manifest.json"
    if not mf.exists():
        sys.exit(f"缺少 {mf}")
    cfg = json.loads(mf.read_text(encoding="utf-8"))

    w = int(cfg.get("width", 1080))
    h = int(cfg.get("height", 1440))
    dsf = args.dsf or int(cfg.get("dsf", 2))
    min_font = args.min_font if args.min_font is not None else float(cfg.get("min_font", 20))
    target_font = float(cfg.get("target_font", 24))
    jpg_width = int(cfg.get("jpg_width", 1440))
    bg = str(cfg.get("background", "FFFFFF")).lstrip("#")
    root_sel = str(cfg.get("root", ".card"))
    meta = list(cfg.get("metadata_selectors", [".top", ".foot", ".rlab"]))

    cards = cfg.get("cards") or []
    if not cards:
        sys.exit("manifest.json 里没有 cards")

    chrome = find_chrome()
    results, failed = [], False

    for c in cards:
        html, name = (root / c["html"]).resolve(), c["out"]
        out = root / f"{name}.png"
        cw_ = int(c.get("width", w))
        ch_ = int(c.get("height", h))
        cdsf = int(c.get("dsf", dsf))
        cbg = str(c.get("background", bg)).lstrip("#")

        if not html.exists():
            print(f"[跳过] {name}: {c['html']} 不存在")
            results.append({"card": name, "fail": ["html 不存在"]})
            failed = True
            continue

        try:
            render_png(chrome, html, out, cw_, ch_, cdsf, cbg)
        except RuntimeError as e:
            print(f"[失败] {name}: {e}")
            results.append({"card": name, "fail": [str(e)]})
            failed = True
            continue

        rep = measure(chrome, html, cw_, ch_, c.get("root", root_sel),
                      meta, target_font * cw_ / REFERENCE_WIDTH)
        fails, warns = judge(rep, float(c.get("min_font", min_font)))
        results.append({"card": name, "png": str(out), "checks": rep,
                        "fail": fails, "warn": warns})

        if not args.no_jpg:
            to_jpg(out, root / f"{name}.jpg", jpg_width)

        print(f"[{'通过' if not fails else '不通过'}] {name}"
              + ("" if "error" in rep else
                 f"  溢出={rep.get('overflow')} 距页脚={rep.get('footer_gap')} "
                 f"最小字号={rep.get('min_font')}"))
        for item in fails:
            print(f"         ✗ {item}")
        for item in warns:
            print(f"         ! {item}")
        failed = failed or bool(fails)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    print("全部通过" if not failed else "有卡片未通过，改版式后重跑", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
