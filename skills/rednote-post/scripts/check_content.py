#!/usr/bin/env python3
"""Check a post directory against the *content* rules that render_cards.py can't see.

Usage:
    python3 check_content.py <post-dir> [--json]

render_cards.py only measures 排版（溢出／压页脚／字号下限／孤字换行）。Everything that
carries *meaning* — 半成品标记、Markdown 加粗、强调色、封面出处 — was enforced by the model
remembering to read SKILL.md, which is exactly where content drifts. This script moves the
machine-checkable subset of those rules into a gate that exits non-zero.

Rules checked (source: references/cards.md, references/copywriting.md, SKILL.md):
    1. 成品里没有半成品标记（待核实／需查证／据称…），正文与卡片一起查
    2. 正文没有 Markdown 加粗（`**字**` 在平台上会原样显示）
    3. 强调色一号是 Volvo Safety Orange #FD6408
    4. 封面素材存在、是 RGB、宽度达到交付画布下限
    5. 成品目录里只有一个标题／正文版本（没有 body-v2.txt 这类并行版本）
    6. 正文没有人设的"反面清单"词，也没有"把条目做成模板"的结构信号——AI 味连接词与行话、
       「→」领起的行动行过多、同一个 emoji 领起多段、破折号过密等。词表与阈值都来自 human-writing
       （human_check.py 通用词表 + references/persona.md 的禁忌词），本文件不再自抄一份
    7. 封面带主题装饰层，且在 manifest 里声明了这本书的 motifs（默认每本都加，见 cards.md）
    8. 装饰层**不是别篇那一版**——两个 post 的装饰路径重合度过高就判红（"不是都加一样的"）

Not machine-checkable here, only warned about: 封面"是不是这本书真实存在的封面"——那需要
一条声明字段，见 manifest.json 的 cover.source；以及 cover.hypothesis——封面点击率是本号
唯一被平台判"有待提升"的一环（references/cards.md 的「本账号实测」），但**封面的哪个属性**
在起作用还没结论，所以现在只要求把这一版赌的哪一条写下来，攒样本。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HALF_BAKED = ["需查证", "待核实", "待考", "据称", "据传", "未经核对", "未逐条核对",
              "存疑", "待补", "待确认", "待定", "TODO", "TBD", "FIXME"]

ACCENT_WANT = "FD6408"
COVER_FLOOR = 900

# 封面的主题装饰层（references/cards.md「封面改造」）。默认每本都加，主题必须从书里来。
DECO_SEL_RE = re.compile(r'<svg[^>]*class="deco"[^>]*>.*?</svg>', re.S)
D_ATTR_RE = re.compile(r'\sd="([^"]+)"')
DECO_MIN_PATHS = 40        # 整版构图 200 上下、最轻的一版 75；低于 40 多半是只粘了一块
DECO_REUSE_JACCARD = 0.5   # 两篇装饰的路径重合到一半，就是"把上一版拿过来用了"

# AI 味词表只有一个来源：human-writing/scripts/human_check.py（通用词表）
# + references/persona.md 的「禁忌词」行（本号特有）。这里 import 它，
# 不再自抄一份——见 human-writing/references/persona-intake.md 第 4 节。
HUMAN_CHECK = None
FALLBACK_AI_SMELL = ["综上所述", "总而言之", "值得注意", "不难发现", "让我们一起", "不得不说",
                     "赋能", "底层逻辑", "抓手", "颗粒度", "打法", "信息爆炸", "快节奏的时代",
                     "由此可见", "众所周知", "一举两得", "锦上添花"]


def _skill_roots() -> list[Path]:
    """跨 skill 找东西的约定顺序：兄弟目录 → $CODEX_HOME/skills → ~/.codex/skills → ~/.agents/skills。
    三个 skill 各有一份**语义相同**的副本（skill 要能单独安装，不能共享一个模块）。
    口径见 docs/ARCHITECTURE.md「跨 skill 调用」。"""
    here = Path(__file__).resolve()
    roots = []
    if len(here.parents) > 1:
        roots.append(here.parents[1].parent)          # …/<skills>/<this-skill> 的上一级
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    roots += [codex_home / "skills", Path.home() / ".codex" / "skills",
              Path.home() / ".agents" / "skills"]
    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _load_human_check():
    """找到兄弟 skill human-writing 的 human_check.py。找不到就返回 None（用兜底词表）。"""
    try:
        import human_check  # type: ignore
        return human_check
    except ImportError:
        pass
    for root in _skill_roots():
        cand = root / "human-writing" / "scripts"
        if (cand / "human_check.py").is_file():
            sys.path.insert(0, str(cand))
            try:
                import human_check  # type: ignore
                return human_check
            except ImportError:
                sys.path.pop(0)
    return None


HUMAN_CHECK = _load_human_check()


def persona_banned() -> list[str]:
    """本号在 references/persona.md 里声明的禁忌词。"""
    if HUMAN_CHECK is None:
        return []
    pf = Path(__file__).resolve().parents[1] / "references" / "persona.md"
    return HUMAN_CHECK.parse_banned(read(pf)) if pf.is_file() else []


TEXT_FILES = ["title.txt", "body.txt"]
VERSION_RE = re.compile(r"^(title|body)[-_]?v?\d+.*\.txt$", re.I)


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def scan_tokens(post: Path) -> tuple[bool, str]:
    targets = [post / n for n in TEXT_FILES]
    build = post / "build"
    if build.is_dir():
        targets += sorted(build.glob("*.html"))
    hits = []
    for f in targets:
        if not f.is_file():
            continue
        txt = read(f)
        for i, line in enumerate(txt.splitlines(), 1):
            for tok in HALF_BAKED:
                if tok in line:
                    hits.append(f"{f.name}:{i} 「{tok}」")
    if hits:
        return False, "；".join(hits[:4])
    return True, f"{len([t for t in targets if t.is_file()])} 个文本文件干净"


def scan_bold(post: Path) -> tuple[bool, str]:
    hits = []
    for n in TEXT_FILES:
        txt = read(post / n)
        if "**" in txt:
            hits.append(n)
    if hits:
        return False, "、".join(hits) + " 里有 `**`（平台原样显示，不渲染加粗）"
    return True, ""


def scan_accent(post: Path) -> tuple[bool, str]:
    build = post / "build"
    css = [p for p in ([build / "card.css"] if build.is_dir() else [])
           + (sorted(build.glob("*.css")) if build.is_dir() else [])]
    css = list(dict.fromkeys(css))
    seen = []
    for f in css:
        if not f.is_file():
            continue
        for m in re.finditer(r"--accent\s*:\s*(#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3})", read(f)):
            seen.append((f.name, m.group(1).lstrip("#").upper()))
    if not seen:
        return False, "build/ 里找不到 --accent 定义，强调色没验过"
    bad = [f"{n}={v}" for n, v in seen if v != ACCENT_WANT]
    if bad:
        return False, f"强调色不是 #{ACCENT_WANT}（{'、'.join(bad)}）——不要用绿色或别的色"
    return True, f"# {ACCENT_WANT}（{seen[0][0]}）"


def png_size(path: Path) -> tuple[int, int, str] | None:
    try:
        head = path.read_bytes()[:33]
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w = int.from_bytes(head[16:20], "big")
    h = int.from_bytes(head[20:24], "big")
    ctype = head[25]
    kind = {0: "gray", 2: "RGB", 3: "palette", 4: "gray+alpha", 6: "RGBA"}.get(ctype, f"type{ctype}")
    return w, h, kind


def image_probe(path: Path) -> tuple[int | None, str | None]:
    png = png_size(path)
    if png:
        return png[0], png[2]
    try:
        from PIL import Image
    except ImportError:
        return None, None
    try:
        with Image.open(path) as im:
            return im.width, im.mode
    except Exception:
        return None, None


def scan_cover(post: Path, manifest: dict) -> tuple[bool, str, list[str]]:
    assets = post / "assets"
    cands = [assets / "cover-src.png", assets / "subject.png",
             assets / "cover.png", assets / "cover.jpg"]
    src = next((c for c in cands if c.is_file()), None)
    if src is None:
        return False, "assets/ 里没有封面素材（cover-src.png / subject.png）", []
    w, mode = image_probe(src)
    if w is None:
        return False, f"{src.name} 读不出尺寸或格式不认", []
    m = (mode or "").upper()
    if m and (m in ("RGBA", "LA", "PA", "CMYK", "P", "PALETTE") or "ALPHA" in m):
        return False, f"{src.name} 是 {mode}，封面要 RGB（不要 alpha／CMYK）", []
    if w < COVER_FLOOR:
        return False, f"{src.name} 宽 {w}px，低于交付画布下限 {COVER_FLOOR}px", []
    notes: list[str] = []
    cov = manifest.get("cover") if isinstance(manifest, dict) else None
    if not (isinstance(cov, dict) and str(cov.get("source") or "").strip()):
        notes.append('manifest.json 没有 cover.source——封面"是不是真封面"没法自动判，补一条声明')
    if not (isinstance(cov, dict) and str(cov.get("hypothesis") or "").strip()):
        notes.append("manifest.json 没有 cover.hypothesis——这一版封面在赌哪个属性？"
                     "写一句，攒到 6 篇才能看出封面点击率的规律（cards.md「本账号实测」）")
    return True, f"{src.name} {w}px {mode}", notes


def deco_paths(html: str) -> set[str]:
    """封面 html 里装饰层用到的 SVG 路径（归一化空白后去重）。

    比对"两篇的装饰是不是同一张"就看这个：坐标一改，路径串就变。
    """
    m = DECO_SEL_RE.search(html)
    if not m:
        return set()
    return {re.sub(r"\s+", "", d) for d in D_ATTR_RE.findall(m.group(0))}


def deco_motifs(manifest: dict) -> list[str]:
    cov = manifest.get("cover") if isinstance(manifest, dict) else None
    deco = cov.get("deco") if isinstance(cov, dict) else None
    if not isinstance(deco, dict):
        return []
    m = deco.get("motifs")
    if isinstance(m, str):
        return [m.strip()] if m.strip() else []
    return [str(x).strip() for x in m if str(x).strip()] if isinstance(m, list) else []


def scan_deco(post: Path, manifest: dict) -> tuple[bool, str]:
    """封面默认带主题装饰层，且要在 manifest 里说清这本书的 motifs。

    装饰是判断，脚本判不了"motif 是不是真从书里拆出来的"；能判的是：有没有、贴全没有、
    有没有**声明**。声明这一栏的作用就是逼出那次判断，并留下可复核的痕迹。
    """
    paths = deco_paths(read(post / "build" / "01-cover.html"))
    motifs = deco_motifs(manifest)
    if not paths:
        return False, ("build/01-cover.html 里没有 <svg class=\"deco\"> 装饰层——"
                       "封面默认带一层，见 references/cards.md「封面改造」")
    if len(paths) < DECO_MIN_PATHS:
        return False, ("装饰层只有 %d 条路径，像是只贴了一部分（整版 200 上下、最轻的预设 75；"
                       "下限 %d）" % (len(paths), DECO_MIN_PATHS))
    if len(motifs) < 2:
        return False, ("manifest.json 缺 cover.deco.motifs——写清这本书拆出来的 2–3 个 motif，"
                       "如 [\"29 首探戈 → 音符\", \"美与伤口 → 玫瑰与刺\"]")
    base = ""
    cov = manifest.get("cover") if isinstance(manifest, dict) else None
    deco = cov.get("deco") if isinstance(cov, dict) else None
    if isinstance(deco, dict) and deco.get("based_on"):
        base = "｜底版：%s" % deco["based_on"]
    return True, "%d 条路径｜motif：%s%s" % (len(paths), "、".join(motifs[:3]), base)


def scan_deco_reuse(post: Path) -> tuple[bool, str]:
    """"都加"不等于"都加一样的"：装饰层不能是别篇那一张。

    拿同工作区里其它 post 的封面比路径集合的 Jaccard。旧篇（还没这项要求之前做的）
    没有装饰层，直接跳过；找不到兄弟目录时只是不检查，不判失败。
    """
    mine = deco_paths(read(post / "build" / "01-cover.html"))
    if not mine:
        return True, ""
    roots = [post.parent]
    pub = post.parent.parent / "published"
    if pub.is_dir():
        roots.append(pub)
    for root in roots:
        try:
            sibs = sorted(root.iterdir())
        except OSError:
            continue
        for sib in sibs:
            if sib == post or not (sib / "build").is_dir():
                continue
            other = deco_paths(read(sib / "build" / "01-cover.html"))
            if not other:
                continue
            j = len(mine & other) / len(mine | other)
            if j >= DECO_REUSE_JACCARD:
                return False, ("装饰层与《%s》重合 %.0f%%——motifs 要从这本书来，别沿用上一版"
                               "（可以拿它当底版，但要重画构图）" % (sib.name, j * 100))
    return True, ""


def scan_persona(post: Path) -> tuple[bool, str]:
    """人设的反面清单 + 出处行在不在。

    AI 味词表来自 human-writing（通用词表 + persona.md 的禁忌词行）。找不到
    human-writing 时退回内置兜底词表，并在结论里标出来——不假装判过。
    """
    body = read(post / "body.txt")
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return True, "没有正文"
    if HUMAN_CHECK is not None:
        rep = HUMAN_CHECK.scan(body, persona_banned())
        # 硬（AI 味词／套装句式）与软（模板化条目、同 emoji 编号、破折号过密…）一起判：
        # 已发成品两个都零命中，所以这里不必留活口。见 human-writing/references/ai-tells.md 第 4 节。
        hits = rep["hard"] + rep["soft"]
        if hits:
            return False, "正文里有不像这个号的说法：" + "、".join(h["hit"] for h in hits[:5])
        note = "词表干净（human-writing：词 + 结构信号）"
    else:
        joined = "\n".join(lines)
        hits = [w for w in FALLBACK_AI_SMELL if w in joined]
        if hits:
            return False, "正文里有不像这个号的说法：" + "、".join(hits[:5])
        note = "词表干净（⚠ 没找到 human-writing，用的是内置兜底词表，可能过时）"
    tail = [ln for ln in lines[-6:] if not ln.startswith("#")]
    has_source = any(ln.startswith("（") or "｜" in ln or "|" in ln for ln in tail)
    if not has_source:
        note += "（提醒：没找到出处行，确认是有意省掉的）"
    return True, note


def scan_versions(post: Path) -> tuple[bool, str]:
    extra = [f.name for f in post.iterdir()
             if f.is_file() and f.suffix.lower() == ".txt"
             and VERSION_RE.match(f.name)]
    if extra:
        return False, "有并行版本：" + "、".join(sorted(extra)) + "（只留一个版本）"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("post", help="成品目录（含 title.txt/body.txt/build/）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    post = Path(args.post).expanduser().resolve()
    if not post.is_dir():
        print(f"没有这个目录：{post}", file=sys.stderr)
        return 2

    manifest = {}
    mf = post / "manifest.json"
    if mf.is_file():
        try:
            manifest = json.loads(read(mf))
        except json.JSONDecodeError:
            manifest = {}

    checks: list[dict] = []
    warns: list[str] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("成品无半成品标记", *scan_tokens(post))
    add("正文无 Markdown 加粗", *scan_bold(post))
    add("强调色是 #FD6408", *scan_accent(post))
    ok, detail, notes = scan_cover(post, manifest)
    add("封面素材可用（RGB／宽度）", ok, detail)
    warns.extend(notes)
    add("封面装饰层按本书重画", *scan_deco(post, manifest))
    add("装饰层不是别篇那一版", *scan_deco_reuse(post))
    add("只有一个版本", *scan_versions(post))
    add("人设：无 AI 味（词＋结构）", *scan_persona(post))

    passed = all(c["ok"] for c in checks)
    rep = {"post": str(post), "pass": passed, "checks": checks, "warnings": warns}

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        for c in checks:
            print(f"{'✓' if c['ok'] else '✗'} {c['name']}" + (f" —— {c['detail']}" if c["detail"] else ""))
        for w in warns:
            print(f"⚠ {w}")
        print("内容合规：全过" if passed else "内容合规：有不过的")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
