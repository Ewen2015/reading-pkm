#!/usr/bin/env python3
"""book-fetch：把「决定读某本书」变成「手上有一个可用的电子书文件」。

四步，中间必须停下来让用户确认格式与封面：

  search    通过外部适配器找候选（适配器命令由 ~/.config/book-fetch/config.json 指定）
  shortlist 剔除李鬼、逐条打分、解析封面与书目，产出可读的对比页
  download  用户确认后才下载，并校验落盘
  queue     登记到待读队列（reading-queue.md）

本脚本**不含任何书源名称**：它只调用适配器。换书源 = 改配置，不改代码。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CONFIG = HOME / ".config/book-fetch/config.json"
PREFS = HOME / ".config/book-fetch/prefs.json"
CACHE = HOME / ".cache/book-fetch"
STATE = HOME / ".local/share/book-fetch"
QUEUE_JSON = STATE / "queue.json"
QUEUE_MD = HOME / "Documents/reading-queue.md"
BOOKS_DIR = HOME / "Documents/books"
SKILLS_DIR = Path(__file__).resolve().parents[2]
OCR_TOOL = SKILLS_DIR / "read-a-book/scripts/ocr_scanned_pdf.py"

UA = "book-fetch/0.1 (personal reading pipeline)"

PDF_PROBE_PAGES = 12
PDF_MIN_CHARS_PER_PAGE = 50

# ---------- 配置 ----------

def load_json(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return dict(default)
    return dict(default)


def load_config() -> dict:
    cfg = load_json(CONFIG, {})
    if not cfg.get("search_cmd"):
        die(
            "还没配置书源适配器。\n"
            f"建一个 {CONFIG}，至少给出 search_cmd 与 download_cmd，例如：\n"
            '{\n  "search_cmd": ["python3", "/path/to/runner.py", "search", "{query}", "--source", "all", "--json"],\n'
            '  "download_cmd": ["python3", "/path/to/runner.py", "download", "{result_id}", "--output", "{output}", "--json"]\n}\n'
            "本脚本不关心适配器背后是哪个书源。"
        )
    return cfg


def die(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def run_adapter(template: list[str], mapping: dict) -> dict:
    argv = [str(t).format(**mapping) for t in template]
    proc = subprocess.run(argv, capture_output=True, text=True)
    raw = proc.stdout.strip()
    if not raw:
        die(f"适配器没有输出（退出码 {proc.returncode}）。\n{proc.stderr.strip()[:800]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 适配器可能把警告打在 stdout，取最后一段 JSON
        i = raw.find("{")
        while i != -1:
            try:
                return json.loads(raw[i:])
            except json.JSONDecodeError:
                i = raw.find("{", i + 1)
        die(f"适配器输出不是 JSON：{raw[:500]}")


# ---------- 李鬼过滤 ----------

EXCLUDE = [
    r"study guide", r"\bsummary\b", r"workbook", r"quicklet", r"cliffs?notes",
    r"sparknotes", r"companion to", r"\(study guide\)", r"works by ",
    r"complete works", r"annotated bibliography", r"shmoop",
    r"导读", r"速读", r"精读笔记", r"摘要", r"解读版", r"节选",
]
EXCLUDE_RE = re.compile("|".join(EXCLUDE), re.I)


def is_impostor(r: dict) -> str | None:
    """返回命中原因；None 表示不是李鬼。"""
    blob = " ".join(str(r.get(k) or "") for k in ("title", "author", "publisher", "publisher_name"))
    m = EXCLUDE_RE.search(blob)
    return m.group(0) if m else None


# ---------- 文本归一化 ----------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)


# ---------- 打分 ----------

FORMAT_RANK = {"epub": 3.0, "pdf": 2.0, "azw3": 1.6, "mobi": 1.5, "djvu": 1.0, "fb2": 1.2, "txt": 0.8}


def parse_size_kb(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s) / 1024 if s > 10000 else float(s)
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?)B?\s*$", str(s), re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v * {"": 1, "K": 1, "M": 1024, "G": 1024 ** 2, "T": 1024 ** 3}[m.group(2).upper()]


def author_match(author_field: str, term: str) -> bool:
    """作者匹配用词序无关的包含：`Sagan, Carl; Druyan Ann` 也算命中 `Carl Sagan`。"""
    a = norm(author_field)
    if not a:
        return False
    toks = [norm(x) for x in re.split(r"[\s,]+", term) if x.strip()]
    toks = [t for t in toks if t] or [norm(term)]
    return all(t in a for t in toks)


def score_candidate(r: dict, target: dict, peers: list[dict], prefs: dict) -> dict:
    """返回 {total, parts:[{name, score, max, note}]}。综合、可解释、可反驳。"""
    parts: list[dict] = []
    ext = (r.get("extension") or "").lower()
    title = r.get("title") or ""
    author = r.get("author") or ""
    ident = str(r.get("identifier") or "")
    blob = norm(title + " " + author)

    # 1. 书目匹配度 /25
    s = 0.0
    notes = []
    t_norm = norm(target["title"])
    if t_norm and t_norm in norm(title):
        s += 10
        if norm(title).startswith(t_norm):
            s += 2
            notes.append("书名以目标开头")
    else:
        notes.append("书名不完全吻合")
    if target["author_terms"] and any(author_match(author, t) for t in target["author_terms"]):
        s += 8
    else:
        notes.append("作者未匹配")
    if target.get("isbns") and any(i in ident.replace("-", "") for i in target["isbns"]):
        s += 5
        notes.append("ISBN 命中已知版本")
    parts.append({"name": "书目匹配", "score": min(s, 25), "max": 25, "note": "；".join(notes)})

    # 2. 版本完整性 /20
    s = 0.0
    notes = []
    if (r.get("publisher") or r.get("publisher_name")):
        s += 5
        notes.append(f"有出版社")
    if r.get("year"):
        s += 4
        notes.append(f"{r['year']}")
    lang = (r.get("language") or "").lower()
    want_lang = (prefs.get("language") or "").lower()
    if lang:
        if want_lang and lang.startswith(want_lang):
            s += 6
            notes.append(f"语言 {lang}（符合偏好）")
        elif not want_lang:
            s += 3
            notes.append(f"语言 {lang}")
        else:
            notes.append(f"语言 {lang}（偏好 {want_lang}）")
    else:
        notes.append("语言未知")
    if ext == "epub":
        s += 5
        notes.append("epub 未删节可能性高")
    parts.append({"name": "版本完整", "score": min(s, 20), "max": 20, "note": "；".join(notes)})

    # 3. 文本可提取性 /20 —— 决定能不能自动做笔记，最重要的技术维度
    s = 0.0
    notes = []
    if ext == "epub":
        s = 18
        notes.append("epub 通常带文字层，可直接抽取")
    elif ext == "pdf":
        s = 11
        notes.append("pdf：文字层未知，下载后必须检测（扫描件要 OCR）")
    elif ext in ("mobi", "azw3"):
        s = 8
        notes.append(f"{ext}：需先转换")
    elif ext:
        s = 5
        notes.append(f"{ext}：格式不理想")
    else:
        s = 4
        notes.append("格式未知")
    parts.append({"name": "文本可提取", "score": s, "max": 20, "note": "；".join(notes)})

    # 4. 文件线索 /15 —— 同格式里明显偏小的，多半删了图或就是节本
    s = 0.0
    notes = []
    kb = parse_size_kb(r.get("size") or r.get("filesize"))
    same = [parse_size_kb(p.get("size") or p.get("filesize")) for p in peers
            if (p.get("extension") or "").lower() == ext]
    same = [x for x in same if x]
    if kb:
        if len(same) >= 3:
            med = statistics.median(same)
            ratio = kb / med if med else 1.0
            if ratio >= 3:
                s += 12
                notes.append(f"{kb/1024:.2f} MB，远大于同格式中位 {med/1024:.2f} MB（有图版的书通常如此，优先）")
            elif ratio >= 0.75:
                s += 12
                notes.append(f"{kb/1024:.2f} MB，与同格式中位相当")
            elif ratio >= 0.4:
                s += 7
                notes.append(f"{kb/1024:.2f} MB，明显小于同格式中位 {med/1024:.2f} MB")
            else:
                s += 2
                notes.append(f"{kb/1024:.2f} MB，远小于同格式中位 {med/1024:.2f} MB（疑删图或节本）")
        else:
            s += 8
            notes.append(f"{kb/1024:.2f} MB")
    else:
        s += 3
        notes.append("文件大小未知")
    parts.append({"name": "文件线索", "score": s, "max": 15, "note": "；".join(notes)})

    # 5. 元数据完整度 /10
    fields = ["title", "author", "language", "extension", "year",
              "publisher", "identifier", "size"]
    have = sum(1 for f in fields if r.get(f) or r.get("publisher_name"))
    s = round(10 * have / len(fields), 1)
    parts.append({"name": "元数据", "score": s, "max": 10,
                  "note": f"{have}/{len(fields)} 个字段有值"})

    # 6. 可得性 /10
    s = 0.0
    if r.get("can_download"):
        s = 10
        note = "可直接下载"
    elif r.get("requires_account"):
        s = 5
        note = "需要登录账号才能下载"
    elif r.get("can_attempt_download"):
        s = 7
        note = "可尝试下载"
    else:
        s = 2
        note = "当前不可下载"
    parts.append({"name": "可得性", "score": s, "max": 10, "note": note})

    total = round(sum(p["score"] for p in parts), 1)
    isbn_hit = bool(target.get("isbns") and any(i in ident.replace("-", "") for i in target["isbns"]))
    return {"total": total, "parts": parts, "isbn_hit": isbn_hit, "size_kb": kb or 0.0}


# ---------- 封面与书目（公共书目 API）----------

def http_json(url: str, timeout: int = 20) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return json.loads(f.read().decode("utf-8", "replace"))
    except Exception:
        return None


def http_bytes(url: str, timeout: int = 25) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as f:
            data = f.read()
            return data if len(data) > 2000 else None   # 占位图只有几十字节
    except Exception:
        return None


def resolve_cover(cand: dict) -> dict:
    """按 ISBN → 书目搜索 的顺序找封面，返回 {cover_path, bibliographic}。"""
    out = {"cover_path": None, "bibliographic": {}}
    isbns = [x.strip() for x in re.split(r"[,\s]+", str(cand.get("identifier") or "")) if x.strip()]
    isbns = [i for i in isbns if re.fullmatch(r"[\dXx-]{10,17}", i)]

    for isbn in isbns:
        d = http_json(f"https://openlibrary.org/isbn/{isbn.replace('-', '')}.json")
        if d:
            out["bibliographic"] = {
                "title": d.get("title"),
                "publishers": d.get("publishers"),
                "publish_date": d.get("publish_date"),
                "number_of_pages": d.get("number_of_pages"),
                "isbn": isbn,
                "source": "openlibrary:isbn",
            }
            covers = d.get("covers") or []
            if covers:
                data = http_bytes(f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg")
                if data:
                    out["cover_path"] = data
                    return out
    # 回退：书名 + 作者搜索
    q = urllib.parse.quote(f"{cand.get('title','')} {cand.get('author','')}".strip())
    d = http_json(f"https://openlibrary.org/search.json?q={q}&limit=1"
                  "&fields=title,author_name,first_publish_year,publisher,cover_i,isbn,number_of_pages_median")
    if d and d.get("docs"):
        doc = d["docs"][0]
        out["bibliographic"] = {
            "title": doc.get("title"),
            "authors": doc.get("author_name"),
            "publishers": doc.get("publisher"),
            "publish_date": doc.get("first_publish_year"),
            "number_of_pages": doc.get("number_of_pages_median"),
            "source": "openlibrary:search",
        }
        if doc.get("cover_i"):
            data = http_bytes(f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg")
            if data:
                out["cover_path"] = data
    return out


# ---------- 命令 ----------

def cmd_search(args) -> int:
    cfg = load_config()
    CACHE.mkdir(parents=True, exist_ok=True)
    res = run_adapter(cfg["search_cmd"], {"query": args.query})
    out = CACHE / "last_search.json"
    out.write_text(json.dumps({"query": args.query, "raw": res}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    n = len(res.get("results") or [])
    print(f"找到 {n} 条候选 → {out}")
    for s in res.get("sources") or []:
        print(f"  · {s.get('source')}: {s.get('status')} {s.get('message') or ''}".rstrip())
    print("下一步：python3 scripts/fetch.py shortlist")
    return 0


def cmd_shortlist(args) -> int:
    src = Path(args.file).expanduser() if args.file else CACHE / "last_search.json"
    if not src.exists():
        die(f"没有搜索结果：{src}\n先跑 search。")
    blob = json.loads(src.read_text(encoding="utf-8"))
    raw = blob.get("raw") or blob
    results = raw.get("results") or []
    if not results:
        die("搜索结果为空。")

    prefs = load_json(PREFS, {"format_rank": ["epub", "pdf"], "language": "english"})
    query = blob.get("query") or args.query or ""
    target = build_target(query, args)

    excluded, kept = [], []
    for r in results:
        reason = is_impostor(r)
        if reason:
            excluded.append({"result": r, "reason": reason})
        else:
            kept.append(r)

    # 去重：同 identifier 或同 hash 只留分数高的
    scored = []
    for r in kept:
        sc = score_candidate(r, target, kept, prefs)
        scored.append({**r, "_score": sc})
    dedup: dict = {}
    for r in scored:
        k = key_of(r)
        if k not in dedup or r["_score"]["total"] > dedup[k]["_score"]["total"]:
            dedup[k] = r
    scored = sorted(dedup.values(),
                    key=lambda x: (-x["_score"]["total"],
                                   not x["_score"]["isbn_hit"],
                                   -x["_score"]["size_kb"]))
    dropped_dupes = len(kept) - len(scored)
    if args.max:
        scored = scored[:args.max]

    cover_dir = CACHE / "covers"
    cover_dir.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(scored):
        got = resolve_cover(r)
        r["_bib"] = got["bibliographic"]
        if got["cover_path"]:
            p = cover_dir / f"{i:02d}-{norm(str(r.get('result_id')))[:24]}.jpg"
            p.write_bytes(got["cover_path"])
            r["_cover"] = str(p)

    payload = {
        "query": query, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": target, "candidates": scored, "excluded": excluded,
        "sources": raw.get("sources") or [],
        "dropped_duplicates": dropped_dupes,
    }
    out = CACHE / "shortlist.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(payload)
    (CACHE / "shortlist.html").write_text(html, encoding="utf-8")
    print_text(payload)
    print(f"\n对比页：{CACHE/'shortlist.html'}\n结构化：{out}")
    return 0


def key_of(r: dict) -> str:
    return norm(str(r.get("identifier") or r.get("hash") or r.get("title") or ""))[:40]


def build_target(query: str, args) -> dict:
    title = args.title or query
    authors = [a.strip() for a in (args.author or "").split(",") if a.strip()]
    isbns = [i.replace("-", "") for i in (args.isbn or "").split(",") if i.strip()]
    terms = authors + (["sagan"] if "sagan" in query.lower() and not authors else [])
    return {"title": title, "author_terms": [t.lower() for t in terms] if terms else [],
            "isbns": isbns, "query": query}


def print_text(payload: dict) -> None:
    print(f"# 候选评分（{payload['query']}）\n")
    print("| # | 分数 | 书名 | 作者 | 格式 | 大小 | 年份 | 封面 |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(payload["candidates"]):
        flag = " ⭐️" if i == 0 else ""
        print(f"| {i+1}{flag} | **{r['_score']['total']}** | {r.get('title','')} | {r.get('author','')} | "
              f"{r.get('extension','')} | {r.get('size','')} | {r.get('year') or '—'} | "
              f"{'有' if r.get('_cover') else '无'} |")
    print("\n## 逐条理由\n")
    for i, r in enumerate(payload["candidates"]):
        print(f"**[{i+1}] {r.get('title','')}** — {r['_score']['total']} 分")
        for p in r["_score"]["parts"]:
            print(f"  - {p['name']} {p['score']}/{p['max']}：{p['note']}")
        if r.get("_bib"):
            b = r["_bib"]
            print(f"  - 书目核对：{b.get('title')}｜{b.get('publishers')}｜{b.get('publish_date')}"
                  f"｜{b.get('number_of_pages')} 页（{b.get('source')}）")
        print()
    if payload["excluded"]:
        print("## 已剔除（疑似李鬼）\n")
        for e in payload["excluded"]:
            print(f"- {e['result'].get('title','')} — 命中「{e['reason']}」")


def render_html(payload: dict) -> str:
    rows = []
    for i, r in enumerate(payload["candidates"]):
        img = (f'<img src="{Path(r["_cover"]).as_uri()}" alt="cover">'
               if r.get("_cover") else '<div class="nocover">无封面</div>')
        parts = "".join(
            f'<li><b>{p["name"]}</b> <span class="sc">{p["score"]}/{p["max"]}</span><br>{p["note"]}</li>'
            for p in r["_score"]["parts"])
        bib = r.get("_bib") or {}
        bibline = "｜".join(str(x) for x in [bib.get("title"), bib.get("publishers"),
                                            bib.get("publish_date"),
                                            (f'{bib["number_of_pages"]} 页' if bib.get("number_of_pages") else None)]
                            if x)
        rows.append(f"""
    <article class="card">
      <div class="cover">{img}</div>
      <div class="meta">
        <h2><span class="rank">#{i+1}</span> <span class="score">{r['_score']['total']}</span> {r.get('title','')}</h2>
        <p class="sub">{r.get('author','')}｜{r.get('extension','').upper()}｜{r.get('size','')}｜{r.get('year') or '年份未知'}｜{r.get('language','')}</p>
        <p class="bib">书目核对：{bibline or '未找到'}</p>
        <ul>{parts}</ul>
      </div>
    </article>""")
    excl = "".join(f"<li>{e['result'].get('title','')} — <i>{e['reason']}</i></li>"
                   for e in payload["excluded"])
    return f"""<!doctype html><meta charset="utf-8">
<title>候选评分 · {payload['query']}</title>
<style>
 body{{font:15px/1.6 -apple-system,"PingFang SC",sans-serif;background:#F3EEE5;color:#111;margin:0;padding:32px}}
 h1{{font-size:22px;margin:0 0 6px}} .q{{color:#666;margin-bottom:22px;font-size:13px}}
 .card{{display:flex;gap:20px;background:#fff;border:1px solid #e3ded4;border-radius:6px;padding:18px;margin-bottom:16px}}
 .cover img{{width:132px;border-radius:3px;display:block}}
 .nocover{{width:132px;height:196px;display:flex;align-items:center;justify-content:center;background:#eee;color:#999;font-size:12px;border-radius:3px}}
 .meta{{flex:1}} h2{{font-size:17px;margin:0 0 4px;font-weight:600}}
 .rank{{color:#999;font-weight:400}} .score{{background:#111;color:#fff;border-radius:3px;padding:1px 7px;font-size:13px}}
 .sub{{color:#555;font-size:13px;margin:0 0 4px}} .bib{{color:#777;font-size:12px;margin:0 0 10px}}
 ul{{list-style:none;padding:0;margin:0}} li{{font-size:12.5px;color:#444;padding:3px 0;border-top:1px solid #f0ece4}}
 .sc{{color:#111;font-variant-numeric:tabular-nums}}
 .excl{{background:#fff;border:1px solid #e3ded4;border-radius:6px;padding:14px 18px;font-size:13px;color:#666}}
</style>
<h1>候选评分</h1><div class="q">{payload['query']} · {payload['generated_at']}</div>
{''.join(rows)}
<div class="excl"><b>已剔除（疑似李鬼/摘编）</b><ul>{excl or '<li>无</li>'}</ul></div>
"""


_NAME_NOISE = (
    # 括号里出现域名样式的 token → 来源信息。
    re.compile(r"[（(][^）)]*[a-z0-9-]+\.[a-z]{2,4}[^）)]*[）)]", re.I),
    # 括号里带句读（。！？，、；）→ 是营销文案，不是书名。
    re.compile(r"[（(][^）)]*[。！？，、；][^）)]*[）)]"),
    # 括号里是纯拉丁短标记、且带连字符/点/数字 → 站点或版本标记（如 `(XXX-Yyy)`）。
    re.compile(r"[（(](?=[^\s）)]{1,25}[）)])[A-Za-z0-9][^\s）)]*[-._0-9][^\s）)]*[）)]"),
    # 末尾**不带括号**的裸来源标记（如 `书名 Z-Library`）：文件名被下载器拼上站点后缀时
    # 常常没有括号。只在书名主体是中文时生效——中文书名后面挂一个纯拉丁、且带
    # 连字符/点/下划线的短 token，基本只可能是来源标记；英文书名（`… Spider-Man`）不受影响。
    re.compile(r"(?<=[\u4e00-\u9fff])\s+[A-Za-z][A-Za-z0-9]*[-._][A-Za-z0-9][A-Za-z0-9._-]*\s*$"),
    # 对称的一种：裸来源标记在最前面（`Z-Library 书名`）。
    re.compile(r"^[A-Za-z][A-Za-z0-9]*[-._][A-Za-z0-9][A-Za-z0-9._-]*\s+(?=[\u4e00-\u9fff])"),
)


def clean_filename(title: str, author: str, ext: str) -> str:
    """生成干净文件名：去掉书名里的来源后缀、站点标记与营销文案。

    三类噪声都来自**文件名/著录本身**，不是书的内容，不该留在书库、笔记和卡片里：
    ① 站点后缀（形如 `书名 (site.sk, site2.sk)`）；② 营销括号（`书名（…鼎力推荐。）`）；
    ③ 纯拉丁的站点/版本标记（`书名 (XXX-Yyy)`）。
    规则**不列举具体站点**——列举会过时，也会把来源信息写进仓库。
    """
    t = str(title or "未命名")
    for pat in _NAME_NOISE:
        prev = None
        while prev != t:          # 一个名字上可能挂着好几块，反复清到不动为止
            prev = t
            t = pat.sub("", t)
    t = re.sub(r"[\\/:*?\"<>|]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" .-_")
    a = re.sub(r"[\\/:*?\"<>|]+", " ", str(author or "")).strip()
    a = re.split(r"[;,]", a)[0].strip()
    name = f"{t} - {a}" if a and a.lower() not in t.lower() else t
    ext = str(ext or "").strip().lstrip(".").lower()
    return (name[:150].strip() or "book") + (f".{ext}" if ext else "")


def cmd_download(args) -> int:
    cfg = load_config()
    payload = json.loads((CACHE / "shortlist.json").read_text(encoding="utf-8"))
    cands = payload["candidates"]
    idx = args.pick - 1
    if not (0 <= idx < len(cands)):
        die(f"--pick 超出范围（1–{len(cands)}）")
    r = cands[idx]
    outdir = Path(args.output).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in outdir.iterdir()} if outdir.exists() else set()
    res = run_adapter(cfg["download_cmd"],
                      {"result_id": r["result_id"], "output": str(outdir)})
    if not res.get("ok", True):
        die(f"适配器报告失败：{json.dumps(res.get('error'), ensure_ascii=False)}")
    after = [p for p in outdir.iterdir() if p.name not in before]
    if not after:
        die("适配器说成功，但目录里没有新文件——不要当成下载完成。")
    f = max(after, key=lambda p: p.stat().st_size)
    if not args.keep_raw_name:
        clean = f.with_name(clean_filename(r.get("title"), r.get("author"), f.suffix.lower()))
        if clean != f and not clean.exists():
            f.rename(clean)
            f = clean
    print(f"已下载：{f}（{f.stat().st_size/1024/1024:.2f} MB）")
    ok, detail = validate_ebook(f)
    print(f"校验：{'✓' if ok else '✗'} {detail}")
    if not ok:
        die("下载的文件没通过校验；删掉后重下，不要把坏文件交给 read-a-book。")
    if f.suffix.lower() == ".pdf":
        print("提醒：pdf 需要检测文字层；若是扫描件，先确认要不要走 OCR 再决定是否用它做笔记。")
    if args.queue:
        cmd_queue(argparse.Namespace(action="add", title=r.get("title"), author=r.get("author"),
                                     file=str(f), result_id=r.get("result_id"),
                                     note=args.note, status="fetched"))
    return 0


def cmd_queue(args) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    q = load_json(QUEUE_JSON, {"items": []})
    items = q.get("items") or []
    if args.action == "add":
        rec = {"title": args.title, "author": args.author, "file": args.file,
               "result_id": args.result_id, "status": args.status or "fetched",
               "note": args.note or "", "added_at": datetime.now().date().isoformat()}
        # 同名 = 同一本，就地更新而不是再叠一条（否则队列会出现两行同一本书）
        for i, it in enumerate(items):
            if norm(it.get("title")) == norm(rec["title"]):
                items[i] = {**it, **{k: v for k, v in rec.items() if v not in ("", None)}}
                break
        else:
            items.append(rec)
    elif args.action == "list":
        pass
    elif args.action == "set":
        # 只更新显式传入的字段：省略的保持原值，否则一次 set 会把备注抹掉。
        patch = {k: v for k, v in (("status", args.status), ("author", args.author),
                                   ("file", args.file), ("note", args.note))
                 if v not in ("", None)}
        if not patch:
            die("set 至少要给一个要改的字段（--status / --author / --file / --note）")
        n = 0
        for it in items:
            if norm(it.get("title")) == norm(args.title):
                it.update(patch)
                n += 1
        if not n:
            die(f"队列里没有《{args.title}》")
    q["items"] = items
    QUEUE_JSON.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    render_queue(items)
    print_queue(items)
    return 0


def render_queue(items: list[dict]) -> None:
    lines = ["# 待读队列", "",
             "由 `book-fetch` 维护，别手改（手改会被下次写入覆盖）。",
             "状态：`want` 想读 · `fetched` 已拿到文件 · `reading` 在读 · `done` 已读完 · `dropped` 放弃", ""]
    lines += ["| 状态 | 书名 | 作者 | 文件 | 备注 |", "| --- | --- | --- | --- | --- |"]
    for it in items:
        f = it.get("file") or "—"
        lines.append(f"| {it.get('status','?')} | {it.get('title','')} | {it.get('author','')} | "
                     f"{Path(f).name if f != '—' else '—'} | {it.get('note','')} |")
    if not items:
        lines.append("| — | （空） | | | |")
    QUEUE_MD.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_queue(items: list[dict]) -> None:
    print(f"# 待读队列（{len(items)} 条）\n")
    for it in items:
        print(f"- [{it.get('status','?')}] {it.get('title','')} — {it.get('author','')}"
              + (f"（{it.get('note')}）" if it.get("note") else ""))
    print(f"\n→ {QUEUE_MD}")


# 受 macOS TCC 保护的子树。进程没拿到「完全磁盘访问权限」时，这些位置下的文件
# 读内容一定失败（stat 有时还能过，open 直接 PermissionError）。
# 判定在这里只是**给动作**，不移动、不改名——和 R18 的 preflight 一个路数。
PROTECTED_TREES = (
    "Library/Mobile Documents",       # iCloud Drive（com~apple~CloudDocs 就在下面）
    "Library/Containers",             # app 沙盒容器（Books 的电子书在这里）
    "Library/Group Containers",
    "Library/Safari",
    "Library/Mail",
    "Library/Messages",
    "Pictures/Photos Library.photoslibrary",
)


def _protection_scope(path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(HOME).as_posix()
    except (ValueError, OSError):
        return None
    for tree in PROTECTED_TREES:
        if rel == tree or rel.startswith(tree + "/"):
            return tree
    return None


def probe_pdf_text(path: Path) -> dict | None:
    """抽样判断 PDF 有没有可用的文字层；探测不了就如实返回 None。

    入口检查过去只看 PDF 头，于是纯扫描件也会报「内容可读 ✓」。这里只回答
    「需不需要 OCR」这一个问题，不评价文字层的 OCR 质量（R47 的另一半仍开放）。
    """
    try:
        import fitz
    except ImportError:
        exe = shutil.which("pdftotext")
        info = shutil.which("pdfinfo")
        if not exe or not info:
            return None
        try:
            pages = int(subprocess.run(
                [info, str(path)], capture_output=True, text=True, check=True
            ).stdout.split("Pages:", 1)[1].splitlines()[0])
        except (subprocess.CalledProcessError, IndexError, ValueError):
            return None
        sampled = min(PDF_PROBE_PAGES, pages)
        indexes = _pdf_sample_indexes(pages, sampled)
        counts: list[int] = []
        for i in indexes:
            out = subprocess.run(
                [exe, "-f", str(i + 1), "-l", str(i + 1), str(path), "-"],
                capture_output=True, text=True,
            )
            if out.returncode != 0:
                return None
            counts.append(len(out.stdout.strip()))
        return {"pages": pages, "sampled": len(indexes),
                "chars_per_page": statistics.median(counts) if counts else 0.0,
                "method": "pdftotext"}

    try:
        with fitz.open(path) as doc:
            pages = doc.page_count
            sampled = min(PDF_PROBE_PAGES, pages)
            # 均匀抽样，避开「只扫了空白扉页」造成的假警报。
            indexes = _pdf_sample_indexes(pages, sampled)
            counts = [len(doc[i].get_text().strip()) for i in indexes]
    except Exception:
        return None
    return {"pages": pages, "sampled": len(indexes),
            "chars_per_page": statistics.median(counts) if counts else 0.0,
            "method": "PyMuPDF"}


def _pdf_sample_indexes(pages: int, sampled: int) -> list[int]:
    """按整本书均匀取页，避免前几页的空白扉页代表整本书。"""
    if sampled <= 1:
        return [0]
    return sorted({round(i * (pages - 1) / (sampled - 1)) for i in range(sampled)})


def report_pdf_text(path: Path) -> None:
    probe = probe_pdf_text(path)
    if probe is None:
        print("文字层：未探测（缺 PyMuPDF / poppler；不要据此判断可直接阅读）")
        return
    cpp = probe["chars_per_page"]
    detail = (f"{probe['pages']} 页，均匀抽样 {probe['sampled']} 页，"
              f"中位 {cpp:.0f} 字/页")
    if cpp < PDF_MIN_CHARS_PER_PAGE:
        print(f"文字层 ✗ {detail}——疑似扫描件，不能直接抽取")
        print(f"  → 先走 OCR：python3 '{OCR_TOOL}' '{path}'")
    else:
        print(f"文字层 ✓ {detail}（只证明有字，不证明 OCR 质量可用）")


EPUB_MIMETYPE = b"application/epub+zip"


def _is_unzipped_epub(p: Path) -> bool:
    """用户交来的"epub 路径"其实是个**解压后的目录**——交书入口的第三种形态（R38）。

    判据取最可靠的两条：`mimetype` 写的是 epub 的 MIME，或者有 `META-INF/container.xml`
    （epub 的规范入口）。看后缀会误判，因为这种目录的后缀常常正好也写成 `.epub`。
    """
    if not p.is_dir():
        return False
    try:
        if (p / "mimetype").is_file() and \
                (p / "mimetype").read_bytes().strip() == EPUB_MIMETYPE:
            return True
    except OSError:
        pass
    return (p / "META-INF" / "container.xml").is_file()


def repack_epub_dir(src: Path, out: Path) -> Path:
    """把解压后的 epub 目录重新打成标准 epub。

    `mimetype` 必须是 zip 里的**第一项**且 **stored（不压缩）**——epub 规范这么要求，
    顺序错了或者被压了，多数阅读器直接判为损坏。其余条目正常 deflate。
    """
    if out.exists():
        die(f"重打包的目标已存在，先处理：{out}")
    files = sorted(f for f in src.rglob("*") if f.is_file())
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        mt = src / "mimetype"
        if mt.is_file():
            z.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
        for f in files:
            rel = f.relative_to(src).as_posix()
            if rel != "mimetype":
                z.write(f, rel, compress_type=zipfile.ZIP_DEFLATED)
    return out


def _repack_target(src: Path, out: str | None) -> Path:
    return Path(os.path.expanduser(out)) if out else src.with_suffix(REPACK_SUFFIX)


REPACK_SUFFIX = ".repacked.epub"


def strip_repack_marker(stem: str) -> str:
    """去掉重打包留下的 `.repacked` 标记。

    这个标记是**我们**加的（目录名已经叫 `X.epub`，重打包只能换个名字），
    不是书名的一部分——不剥掉就会一路带进书名、笔记和小红书标题。
    """
    marker = REPACK_SUFFIX[: -len(".epub")]      # ".repacked"
    return stem[: -len(marker)] if stem.endswith(marker) else stem


def resolve_source(src: Path, repackage: bool = False,
                   out: str | None = None) -> Path:
    """把"用户交来的路径"变成一条真能读的文件路径；目录形态就地重打包。

    只在 `repackage=True` 时写盘，且**原目录一律保留**——用户可能还要。
    """
    if not _is_unzipped_epub(src):
        return src
    target = _repack_target(src, out)
    repack_epub_dir(src, target)
    print(f"交来的是**解压后的 epub 目录**，已重新打包 ✓  {target}")
    print(f"原目录保留未动：{src}")
    return target


def cmd_check_source(args) -> int:
    """交书入口的前置检查：这个路径现在读得到吗？只报告，不动文件。"""
    p = Path(os.path.expanduser(args.path))
    print(f"路径：{p}")
    try:
        exists = p.exists()
    except OSError as e:
        print(f"连 stat 都被拒：{e}")
        exists = False
    if not exists:
        print("不存在 ✗")
        return 1
    if p.is_dir():
        if not _is_unzipped_epub(p):
            print("这是个目录不是文件 ✗（epub/pdf 要指到具体文件）")
            return 1
        if not getattr(args, "repackage", False):
            print("这是个目录不是文件——但它**是解压后的 epub**，直接重打一次就行 ✗")
            print("  不用手工 zip，加一个开关：")
            print(f'    fetch.py check-source --path "{p}" --repackage')
            print("  重打包只写一个新的 .epub，原目录保留不动。")
            return 1
        try:
            p = resolve_source(p, repackage=True, out=getattr(args, "out", None))
        except OSError as e:
            print(f"重打包失败 ✗（{e}）")
            return 1

    scope = _protection_scope(p)
    try:
        size = p.stat().st_size
    except OSError as e:
        print(f"拿不到大小 ✗（{e}）")
        return 1
    print(f"存在 ✓  大小 {size / 1024 / 1024:.2f} MB")
    if size == 0:
        print("大小是 0 ✗——多半只是 iCloud 占位符，正文没落到本地\n"
              "  → 在 Finder 里打开它一次，或右键「立即下载」，再重跑")
        return 1

    try:
        with open(p, "rb") as f:
            head = f.read(4)
    except PermissionError:
        print("内容读不到 ✗（PermissionError）")
        if scope:
            print(f"  它落在受 TCC 保护的子树：~/{scope}\n"
                  "  不是路径写错了，是 macOS 不让当前进程读。两条修复动作：\n"
                  "  ① 最省事：Finder 里把它拖到不受保护的目录（本流程用 ~/Documents/books），再重跑；\n"
                  "  ② 一劳永逸：系统设置 → 隐私与安全性 → 完全磁盘访问权限，"
                  "给「正在跑这个 agent 的那个 app」打勾（不是给 Terminal），然后完全退出并重开它。")
        else:
            print("  它不在已知的保护子树里，更像权限位 / ACL 的问题。先看一眼：\n"
                  f"    ls -l@ '{p}'        # 每一位还有没有 r？有没有 deny 的 ACL\n"
                  "  补回读权限，或者复制一份到别处再重跑。")
        return 1
    except OSError as e:
        print(f"内容读不到 ✗（{e}）")
        return 1

    if head[:2] == b"PK":
        kind = "zip/epub ✓"
    elif head[:4] == b"%PDF":
        kind = "pdf ✓"
    else:
        kind = f"既不是 zip 也不是 pdf（magic={head!r}）——确认一下格式"
    print(f"内容可读 ✓  {kind}")
    if p.suffix.lower() == ".pdf":
        report_pdf_text(p)
    if scope:
        print(f"注意：它在受保护子树 ~/ {scope} 下。现在读得到，换个进程/换台机器可能就读不到；"
              "建议挪到 ~/Documents/books 再进流程。")
    return 0


def _epub_metadata(path: Path) -> dict:
    """从 epub 自己的著录里取书名与作者（dc:title / dc:creator）。

    这个文件已经躺在手里了，书名不该再靠猜文件名——文件名是下载器起的，著录是出版社给的。
    取不到就返回空 dict，由调用方回退，不抛异常。
    """
    out: dict = {}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            opf = None
            if "META-INF/container.xml" in names:
                for rf in ET.fromstring(z.read("META-INF/container.xml")).iter():
                    if rf.tag.endswith("rootfile") and rf.get("full-path"):
                        opf = rf.get("full-path")
                        break
            if not opf:
                cands = [n for n in names if n.lower().endswith(".opf")]
                opf = cands[0] if cands else None
            if not opf:
                return out
            for el in ET.fromstring(z.read(opf)).iter():
                tag = el.tag.split("}")[-1]
                val = (el.text or "").strip()
                if not val:
                    continue
                if tag == "title" and "title" not in out:
                    out["title"] = val
                elif tag == "creator" and "creator" not in out:
                    out["creator"] = val
                elif tag == "language" and "lang" not in out:
                    out["lang"] = val
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return out
    return out


def validate_ebook(p: Path) -> tuple[bool, str]:
    """下载/收编后的最小结构校验：坏文件不要进 read-a-book。

    只看能一击命中"下载截断"的信号：zip 能不能打开、container/opf 在不在、
    spine 有没有条目；PDF 看头尾。文字层质量不在这里判（read-a-book 会抽）。
    """
    try:
        size = p.stat().st_size
    except OSError as e:
        return False, f"读不到文件大小：{e}"
    if size < 1024:
        return False, f"文件只有 {size} 字节，像空文件/占位符"

    suffix = p.suffix.lower()
    if suffix == ".epub":
        try:
            with zipfile.ZipFile(p) as z:
                names = set(z.namelist())
                if "META-INF/container.xml" not in names:
                    return False, "EPUB 缺 META-INF/container.xml"
                opf = None
                for rf in ET.fromstring(z.read("META-INF/container.xml")).iter():
                    if rf.tag.endswith("rootfile") and rf.get("full-path"):
                        opf = rf.get("full-path")
                        break
                if not opf:
                    return False, "EPUB 的 container.xml 里没有 rootfile"
                if opf not in names:
                    return False, f"EPUB 的 OPF 不在包里：{opf}"
                spine = sum(1 for el in ET.fromstring(z.read(opf)).iter()
                            if el.tag.endswith("itemref"))
                if spine == 0:
                    return False, "EPUB spine 是 0 项，正文没进包（下载截断）"
                if spine < 5:
                    return True, f"EPUB zip 完整，spine 只有 {spine} 项（偏少，确认不是节本）"
                return True, f"EPUB zip 完整，spine {spine} 项"
        except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError) as e:
            return False, f"EPUB 读不开（{type(e).__name__}: {e}），多半下载截断"
    if suffix == ".pdf":
        try:
            with open(p, "rb") as f:
                head = f.read(5)
                f.seek(max(0, size - 8192))
                tail = f.read()
            if head != b"%PDF-":
                return False, "PDF 头不是 %PDF-"
            if b"%%EOF" not in tail:
                return False, "PDF 末尾没有 %%EOF，像下载截断"
            return True, "PDF 头尾完整（文字层未检测）"
        except OSError as e:
            return False, f"PDF 读不开：{e}"
    return True, f"{suffix or '无后缀'} 不做结构校验（只查了非空）"


def cmd_validate(args) -> int:
    p = Path(os.path.expanduser(args.path))
    ok, detail = validate_ebook(p)
    print(f"{'✓' if ok else '✗'} {p.name}：{detail}")
    return 0 if ok else 1


_PLACEHOLDER_STEMS = {"未命名", "book", "untitled"}


def _looks_uninformative(stem: str) -> bool:
    """文件名是不是"没有信息量"（哈希串、纯数字、占位名）——是的话别拿它当书名。"""
    s = stem.strip()
    if len(s) < 2:
        return True
    if s.lower() in _PLACEHOLDER_STEMS:              # clean_filename 补出来的占位名
        return True
    if re.fullmatch(r"[0-9a-fA-F]{8,}", s):          # 下载器给的哈希
        return True
    if re.fullmatch(r"[\d\s.\-_]+", s):              # 纯数字（ISBN、序号）
        return True
    return False


def cmd_adopt(args) -> int:
    """把用户直接交来的文件收编进书库：验可达 → 落位 → 洗名字 → 登记队列。

    走的是和 `download` 同一条尾巴。**只复制不移动**：用户手里那份原件不动。
    """
    src = Path(os.path.expanduser(args.path))
    if _is_unzipped_epub(src):
        try:
            src = resolve_source(src, repackage=True,
                                 out=getattr(args, "out", None))
        except OSError as e:
            die(f"重打包失败：{e}")
    rc = cmd_check_source(argparse.Namespace(path=str(src)))
    if rc != 0:
        print("\n→ 先把上面的问题解决，再重新 adopt。（这一步没有任何写入）")
        return rc
    ok, detail = validate_ebook(src)
    print(f"校验：{'✓' if ok else '✗'} {detail}")
    if not ok:
        die("交来的文件没通过校验；换一个完整文件再 adopt。")

    outdir = Path(os.path.expanduser(args.into)).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    target = src
    if src.resolve().parent != outdir.resolve():
        target = outdir / src.name
        if target.exists():
            die(f"书库里已经有同名文件，先处理重名：{target}")
        shutil.copy2(src, target)
        print(f"\n已复制进书库：{target}（原件没动）")

    meta = _epub_metadata(target) if target.suffix.lower() == ".epub" else {}
    raw_stem = clean_filename(strip_repack_marker(target.stem), "", "")
    # 文件名常写成「书名 - 作者」。后半段只有在**著录能对上**（或根本没有著录）时才当作者，
    # 否则会把 `Sapiens - A Brief History` 这种真副标题砍掉。
    creator = str(meta.get("creator") or "").strip()
    tail_author = ""
    if " - " in raw_stem:
        head, tail = raw_stem.rsplit(" - ", 1)
        if tail and len(tail) <= 30 and (not creator or norm(tail) == norm(creator)):
            tail_author, raw_stem = tail, head
    # 文件名常常是下载/下载器拼的营销长串（括号还会被文件系统吃掉，清洗规则就失效了），
    # 而 dc:title 是出版社给的著录——两者都清洗一遍，著录优先。
    meta_title = clean_filename(str(meta.get("title") or ""), "", "")
    title = args.title or (meta_title if not _looks_uninformative(meta_title) else "")
    if not title or _looks_uninformative(title):
        title = raw_stem or meta.get("title") or "未命名"
    author = args.author or creator or tail_author
    clean = target.with_name(clean_filename(title, author, target.suffix.lower()))
    if clean != target:
        if clean.exists():
            die(f"洗后的名字已经存在，先处理重名：{clean}")
        target.rename(clean)
        print(f"已洗掉名字里的来源/文案：{clean.name}")
        target = clean
    print(f"书库文件：{target}")
    if target.suffix.lower() == ".pdf":
        print("提醒：pdf 需要检测文字层；若是扫描件，先确认要不要走 OCR 再决定是否用它做笔记。")
    cmd_queue(argparse.Namespace(action="add", title=title, author=author or None,
                                 file=str(target), result_id="external",
                                 note=args.note, status=args.status))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="book-fetch：找书、评分、确认、下载、登记队列")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="调用外部适配器找候选")
    p.add_argument("query")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("validate", help="校验下载/收编的电子书是不是完整可用")
    p.add_argument("--path", required=True)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("check-source", help="交书入口的前置检查：这个路径读得到吗（只报告）")
    p.add_argument("--path", required=True, help="用户交来的 epub/pdf 路径")
    p.add_argument("--repackage", action="store_true",
                   help="路径是解压后的 epub 目录时就地重打包成 .epub")
    p.add_argument("--out", default=None, help="重打包的输出路径（默认同名 .repacked.epub）")
    p.set_defaults(fn=cmd_check_source)

    p = sub.add_parser("adopt", help="收编用户直接交来的文件：验可达 → 落位 → 洗名字 → 登记")
    p.add_argument("--path", required=True, help="用户交来的 epub/pdf 路径")
    p.add_argument("--into", default=str(BOOKS_DIR), help=f"书库目录，默认 {BOOKS_DIR}")
    p.add_argument("--title", help="覆盖书名（文件名和著录都不可信时用）")
    p.add_argument("--author", help="覆盖作者")
    p.add_argument("--status", default="fetched", help="登记状态，默认 fetched")
    p.add_argument("--note", default="")
    p.add_argument("--out", default=None, help="重打包的输出路径（默认同名 .repacked.epub）")
    p.set_defaults(fn=cmd_adopt)

    p = sub.add_parser("shortlist", help="剔除李鬼、打分、解析封面与书目")
    p.add_argument("--file", help="用指定的搜索结果文件，默认用最近一次")
    p.add_argument("--query")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--isbn")
    p.add_argument("--max", type=int, default=8)
    p.set_defaults(fn=cmd_shortlist)

    p = sub.add_parser("download", help="下载用户选中的那条（必须由用户确认后调用）")
    p.add_argument("--pick", type=int, required=True, help="shortlist 里的序号，从 1 开始")
    p.add_argument("--output", required=True)
    p.add_argument("--queue", action="store_true", help="下载成功后登记到待读队列")
    p.add_argument("--note", default="")
    p.add_argument("--keep-raw-name", action="store_true",
                   help="不重命名（默认会去掉文件名里的来源后缀）")
    p.set_defaults(fn=cmd_download)

    p = sub.add_parser("queue", help="待读队列")
    p.add_argument("action", choices=["add", "list", "set"])
    p.add_argument("--title"); p.add_argument("--author"); p.add_argument("--file")
    p.add_argument("--result_id"); p.add_argument("--status"); p.add_argument("--note")
    p.set_defaults(fn=cmd_queue)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
