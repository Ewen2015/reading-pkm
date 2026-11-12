#!/usr/bin/env python3
"""排期计算：把发布计划、本地库存、平台定时队列合成一张未来排期表。

只做排期判断，不写文案、不做图。`ready` 全绿且 plan.json 里
`publish_policy=auto` 时，本 skill 才有权直接走 rednote-post 发布，不必再问一次。

用法：
  python3 plan.py init   [--workspace W]                 建计划骨架
  python3 plan.py report [--workspace W] [--state F]     出排期与缺口报告
                         [--weeks 4] [--json]
  python3 plan.py ready  [--workspace W] [--post D]      发布就绪检查
                         [--state F] [--weeks 4] [--json]

workspace 布局（默认 $XHS_WORKSPACE，未设则 ~/Documents/rednote）：
  <workspace>/
  |-- plan.json          发布计划（频率/时段/栏目/目标）
  |-- topics.md          选题池，一行一条，`- [ ]` 未用 / `- [x]` 已用
  |-- posts/             每个笔记一个 rednote-post 目录
  `-- state/             xhs_state.py 的输出（可选，喂给 --state）

字段与口径见 references/plan-schema.md。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

DEFAULT_TARGETS = {"ready_weeks": 2, "semi_weeks": 4, "topic_weeks": 6}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TITLE_MAX = 20          # xhs.py 硬失败线，与 CLI 一致
BODY_MAX = 1000         # CLI 只是警告，所以这里也只算 warn
SLOT_LEAD_MIN = 30      # 定时发布要晚于当前时间 30 分钟以上
SLOT_MATCH_H = 6        # 与 build_schedule 同口径：6 小时内算同一个档位
# 小红书的「定时发布」只收「今天 + 13 天」以内的时刻（2026-10-07 实测：设 10-20 存成
# 10-20，设 10-21 掉回默认值）。超窗的表现极具欺骗性：表单**回读**仍然是你填的时刻，
# `schedule` 因此报"已开启、时间正确"，但平台提交时把它丢掉、退回自己的默认值
# 「今天 + 1 小时、向上取到 5 分钟」——2026-10-07 就把 10-21 22:00 的档排成了当天 22:45。
# 所以空档落在窗口外时不能报"可自动发布"，只能判不过、把成品留着等窗口放开。记 R60。
SCHEDULE_WINDOW_DAYS = 13
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

PLAN_TEMPLATE = {
    "account": "",
    "cadence": {
        "posts_per_week": 2,
        "weekdays": [2, 6],
        "slots": ["20:00"],
        "note": "weekdays 用 0=周一 … 6=周日；slots 是当天几点发",
    },
    "pillars": [
        {"name": "读书笔记", "share": 0.6},
        {"name": "思维模型", "share": 0.4},
    ],
    "targets": dict(DEFAULT_TARGETS),
    "paused": False,
    "publish_policy": "confirm",
}

TOPICS_TEMPLATE = """# 选题池

一行一条。`- [ ]` 未用，`- [x]` 已用。选题多于成品是正常的——
每条成品背后通常要废掉一两个选题，池子太浅就会断更。

- [ ] 示例选题：为什么读了很多书还是做不好决定
"""


def die(msg: str, code: int = 2):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(code)


def workspace(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("XHS_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "Documents" / "rednote").resolve()


def cmd_init(args) -> int:
    ws = workspace(args.workspace)
    (ws / "posts").mkdir(parents=True, exist_ok=True)
    (ws / "state").mkdir(parents=True, exist_ok=True)
    plan = ws / "plan.json"
    topics = ws / "topics.md"
    if plan.exists():
        print(f"已存在，未覆盖：{plan}")
    else:
        plan.write_text(json.dumps(PLAN_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"已创建 {plan} —— 先把它改成你的实际频率和时段")
    if not topics.exists():
        topics.write_text(TOPICS_TEMPLATE, encoding="utf-8")
        print(f"已创建 {topics}")
    return 0


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as e:
        die(f"{path} 不是合法 JSON：{e}")


def has_text(path: Path) -> bool:
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, UnicodeDecodeError):
        return False


def classify_posts(ws: Path) -> tuple[list[dict], list[dict]]:
    """把 posts/ 下的目录分成"可直接发"和"半成品"。

    可直接发的判据来自 rednote-post 的交付物：标题、正文、至少一张成图。
    只有目录没有内容不算库存——那只是占了个名字。
    """
    ready, semi = [], []
    posts_dir = ws / "posts"
    if not posts_dir.is_dir():
        return ready, semi
    for d in sorted(posts_dir.iterdir()):
        if not d.is_dir():
            continue
        title = has_text(d / "title.txt")
        body = has_text(d / "body.txt")
        images = [f for f in d.iterdir()
                  if f.is_file() and f.suffix.lower() in IMAGE_EXT]
        manifest = (d / "manifest.json").exists()
        entry = {"name": d.name, "title": _first_line(d / "title.txt"),
                 "images": len(images), "manifest": manifest,
                 "missing": [n for n, ok in (("标题", title), ("正文", body),
                                             ("配图", bool(images))) if not ok]}
        (ready if title and body and images else semi).append(entry)
    return ready, semi


def _first_line(path: Path) -> str | None:
    if not has_text(path):
        return None
    return path.read_text(encoding="utf-8").strip().splitlines()[0][:60]


def count_topics(ws: Path) -> tuple[int, int]:
    f = ws / "topics.md"
    if not f.exists():
        return 0, 0
    open_n = done_n = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("- [ ]"):
            open_n += 1
        elif s.startswith("- [x]") or s.startswith("- [X]"):
            done_n += 1
    return open_n, done_n


def iter_slots(start: datetime, weeks: int, weekdays: list[int],
               slots: list[str]) -> list[datetime]:
    out = []
    day = start.date()
    for i in range(weeks * 7 + 1):
        d = day + timedelta(days=i)
        if d.weekday() not in weekdays:
            continue
        for s in slots:
            hh, mm = (int(x) for x in s.split(":"))
            dt = datetime.combine(d, dtime(hh, mm))
            if dt > start:
                out.append(dt)
    return sorted(out)


def parse_post_time(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def _skill_roots() -> list[Path]:
    """跨 skill 找东西的约定顺序：兄弟目录 → $CODEX_HOME/skills → ~/.codex/skills → ~/.agents/skills。
    三个 skill 各有一份**语义相同**的副本（skill 要能单独安装，不能共享一个模块）。
    口径见 docs/guides/ARCHITECTURE.md「跨 skill 调用」。"""
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


def find_skill_file(skill: str, *parts: str) -> Path | None:
    for root in _skill_roots():
        cand = root.joinpath(skill, *parts)
        if cand.is_file():
            return cand
    return None


def find_render_cards() -> Path | None:
    """找 rednote-post 的实测脚本；找不到就不能给版式背书。"""
    return find_skill_file("rednote-post", "scripts", "render_cards.py")


def find_check_content() -> Path | None:
    """找 rednote-post 的内容合规脚本；找不到就不能给"内容层"背书。"""
    return find_skill_file("rednote-post", "scripts", "check_content.py")


def occupancy_path(args, ws: Path) -> Path | None:
    """在哪里找"已占用的档位"。

    不给 --state 时**默认用 workspace 里那份**（<ws>/state/state.json），
    而不是当作"没有任何东西被占用"——后者会把早就被占的档位报成空档。
    """
    if getattr(args, "state", None):
        return Path(args.state).expanduser()
    cand = ws / "state/state.json"
    return cand if cand.is_file() else None


def next_free_slot(now: datetime, weeks: int, weekdays: list[int],
                   slots_txt: list[str], scheduled: list[dict]):
    """计划时段里第一个没被平台定时占用的空档。"""
    occupied = [t for t in (parse_post_time(s.get("time")) for s in scheduled)
                if t is not None]
    for dt in iter_slots(now + timedelta(minutes=SLOT_LEAD_MIN),
                         weeks, weekdays, slots_txt):
        if any(abs((dt - t).total_seconds()) <= SLOT_MATCH_H * 3600 for t in occupied):
            continue
        return dt
    return None


def run_render_check(post_dir: Path, script: Path | None) -> tuple[bool, str]:
    """跑一遍 rednote-post 的实测；只有它全绿才算版式过关。"""
    if script is None:
        return False, "找不到 rednote-post/scripts/render_cards.py，版式没验过"
    import subprocess
    try:
        proc = subprocess.run([sys.executable, str(script), str(post_dir)],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"实测跑不起来：{e}"
    if proc.returncode == 0:
        return True, "全部通过"
    bad = [ln.strip() for ln in proc.stdout.splitlines() if "不通过" in ln or "✗" in ln]
    return False, "；".join(bad[:4]) or "有卡片未通过，看 render_cards.py 的输出"


def run_content_check(post_dir: Path, script: Path | None) -> tuple[bool, str]:
    """跑一遍内容合规：半成品标记、加粗、强调色、封面素材、版本唯一。"""
    if script is None:
        return False, "找不到 rednote-post/scripts/check_content.py，内容层没验过"
    import subprocess
    try:
        proc = subprocess.run([sys.executable, str(script), str(post_dir)],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"内容合规跑不起来：{e}"
    if proc.returncode == 0:
        return True, "全部通过"
    bad = [ln.strip() for ln in proc.stdout.splitlines()
           if ln.startswith("✗") or "✗" in ln]
    return False, "；".join(bad[:4]) or "内容合规有未通过项看 check_content.py 的输出"


def build_schedule(slots: list[datetime], scheduled: list[dict],
                   ready: list[dict]) -> tuple[list[dict], list[dict]]:
    """平台已定时的先占位，剩下的用本地成品按顺序补。"""
    rows = [{"at": s, "source": None, "title": None} for s in slots]
    unmatched = []
    for item in scheduled:
        at = parse_post_time(item.get("time"))
        if at is None:
            unmatched.append(item)
            continue
        best, best_gap = None, None
        for r in rows:
            if r["source"] is not None:
                continue
            gap = abs((r["at"] - at).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = r, gap
        if best is not None and best_gap is not None and best_gap <= 6 * 3600:
            best["source"] = "scheduled"
            best["title"] = item.get("title")
            best["noteId"] = item.get("noteId")
        else:
            unmatched.append(item)

    i = 0
    for r in rows:
        if r["source"] is not None:
            continue
        if i < len(ready):
            r["source"] = "local"
            r["title"] = ready[i]["title"] or ready[i]["name"]
            r["post"] = ready[i]["name"]
            i += 1
    return rows, unmatched


# 新笔记前几天的阅读量还在涨，拿"阅读/天"去比会把刚发的误判成最好的时段。
SETTLE_DAYS = 3


def hour_buckets(rows: list[dict]) -> tuple[list[dict], int]:
    """按发布小时聚合历史数据，返回 (分档, 因太新被排除的篇数)。

    阅读量是累计值：昨天发的天然比去年发的低，所以这里同时给
    「阅读/天」，否则会把"发得早"误判成"时段好"。但发布不满
    SETTLE_DAYS 天的笔记两个指标都不可比，直接排除。
    """
    now = datetime.now()
    groups: dict[int, list[dict]] = {}
    too_fresh = 0
    for r in rows:
        at = parse_post_time(r.get("time"))
        if at is None:
            continue
        age_days = (now - at).total_seconds() / 86400.0
        if age_days < SETTLE_DAYS:
            too_fresh += 1
            continue
        reads = r.get("reads") or 0
        groups.setdefault(at.hour, []).append(
            {"reads": reads, "per_day": reads / age_days,
             "engage": (r.get("likes") or 0) + (r.get("collects") or 0)}
        )
    out = []
    for h in sorted(groups):
        g = groups[h]
        out.append({
            "hour": h,
            "n": len(g),
            "median_reads": statistics.median(x["reads"] for x in g),
            "median_reads_per_day": round(statistics.median(x["per_day"] for x in g), 1),
            "median_engage": statistics.median(x["engage"] for x in g),
        })
    return out, too_fresh


def reminder_advice(buffer_weeks: float, ppw: float, target_ready: float) -> dict:
    if ppw <= 0:
        return {"mode": "ad-hoc", "why": "计划里没填频率（posts_per_week=0），无法判断节奏。"}
    if buffer_weeks < 1:
        return {"mode": "cron",
                "why": f"成品缓冲只有 {buffer_weeks:.1f} 周，低于 1 周的最低线，随时会断更。",
                "frequency": "每周一次 + 每次发出后立刻补货"}
    if buffer_weeks < target_ready:
        return {"mode": "cron",
                "why": f"成品缓冲 {buffer_weeks:.1f} 周，低于 {target_ready:.0f} 周的目标线。",
                "frequency": "每周一次"}
    if buffer_weeks >= 4 and ppw <= 1:
        return {"mode": "ad-hoc",
                "why": f"缓冲 {buffer_weeks:.1f} 周且频率只有 {ppw:g} 篇/周，定期提醒只会变成噪音。"}
    return {"mode": "ad-hoc",
            "why": f"缓冲 {buffer_weeks:.1f} 周，够用；等掉到 {target_ready:.0f} 周以下再开定期提醒。"}


def render_text(rep: dict) -> str:
    L = []
    a = L.append
    c = rep["cadence"]
    inv = rep["inventory"]
    tgt = rep["targets"]
    ppw = c["posts_per_week"]
    a(f"# 排期体检 {rep['generated_at']}")
    a("")
    a(f"计划：每周 {ppw:g} 篇 · "
      f"{'/'.join(WEEKDAY_CN[d] for d in c['weekdays'])} · "
      f"{'/'.join(c['slots'])}")
    a(f"库存：可直接发 {inv['ready_count']} 篇 · 半成品 {inv['semi_count']} 篇 · "
      f"选题池 {inv['topics_open']} 条未用")
    a(f"覆盖率：现库存可撑 {rep['coverage']['weeks']:.1f} 周"
      f"（目标 {tgt['ready_weeks']} 周 = {inv['target_ready']:.0f} 篇）")
    if rep.get("paused"):
        a("")
        a("**计划处于停更状态（paused=true）**——下面不排期、不报空档。"
          "要恢复更新就把 plan.json 里的 paused 改回 false。")
    a("")
    if rep.get("paused"):
        return "\n".join(L)
    a(f"## 未来 {rep['horizon_weeks']} 周排期")
    a("")
    a("| 时间 | 内容 | 来源 |")
    a("| --- | --- | --- |")
    for r in rep["schedule"]:
        at = datetime.fromisoformat(r["at"])
        when = f"{at:%Y-%m-%d} {WEEKDAY_CN[at.weekday()]} {at:%H:%M}"
        src = r["source"]
        if src == "scheduled":
            label, title = "已定时", r["title"] or "(无标题)"
        elif src == "off-plan":
            label, title = "已定时·计划外", r["title"] or "(无标题)"
        elif src == "local":
            label, title = "本地成品", r["title"] or ""
        else:
            label, title = "**缺口**", "——"
        a(f"| {when} | {title} | {label} |")
    a("")
    g = rep["gap"]
    if g["empty_slots"]:
        a(f"**未来 {rep['horizon_weeks']} 周有 {g['empty_slots']} 个空档。**"
          f"要回到 {tgt['ready_weeks']} 周缓冲，还需要 {g['to_target']} 篇成品。")
    else:
        a(f"未来 {rep['horizon_weeks']} 周排满，没有空档。")
    a("")
    a("## 素材积累量建议")
    a("")
    a("| 层级 | 目标 | 现在 | 还缺 |")
    a("| --- | --- | --- | --- |")
    rows = [
        ("可直接发（过实测、能立刻排）", inv["target_ready"], tgt["ready_weeks"],
         inv["ready_count"]),
        ("半成品（有选题有素材）", inv["target_semi"], tgt["semi_weeks"],
         inv["semi_count"]),
        ("选题池（未用）", inv["target_topics"], tgt["topic_weeks"],
         inv["topics_open"]),
    ]
    for name, goal, weeks, have in rows:
        need = max(0, int(goal - have + 0.999))
        a(f"| {name} | {goal:.0f}（{weeks} 周） | {have} | {need or '—'} |")
    a("")
    a("成品按 2 周缓冲，是为了扛一次出差或生病还不断更；选题池按 6 周，"
      "因为一条成品通常要废掉一两次选题，池子浅了就会临时凑内容。")
    if rep.get("timing"):
        t = rep["timing"]
        a("")
        a(f"## 账号自己的时段数据（可用样本 {t['usable']} 篇"
          f"{'，另有 %d 篇太新已排除' % t['too_fresh'] if t['too_fresh'] else ''}）")
        a("")
        a("| 发布小时 | 样本 | 阅读中位数 | 阅读/天 中位数 | 互动中位数 |")
        a("| --- | --- | --- | --- | --- |")
        for b in t["buckets"]:
            a(f"| {b['hour']:02d}:00 | {b['n']} | {b['median_reads']:.0f} | "
              f"{b['median_reads_per_day']:.1f} | {b['median_engage']:.0f} |")
        if t["thin"]:
            a("")
            a("样本太薄（每档至少 3 篇才有意义）——先按 references/timing.md 的"
              "基线时段发，攒够样本再按上表调整。")
    if rep.get("platform"):
        a("")
        a("## 平台状态")
        if rep["platform"].get("partial"):
            a(f"- ⚠️ **读到的队列不完整**（{rep['platform'].get('cards_scanned')} / "
              f"{rep['platform'].get('expected_total')} 篇），下面的缺口与覆盖率"
              f"都不可信，先把 Chrome 切到前台重扫 `xhs_state.py`。")
        a(f"- 定时发布队列：{rep['platform']['scheduled_count']} 篇")
        a(f"- 审核中：{rep['platform']['in_review_count']} 篇")
        dc = rep["platform"].get("draft_counts") or {}
        if dc:
            parts = " / ".join(f"{k} {v}" for k, v in dc.items())
            a(f"- 草稿箱（浏览器本地，清浏览器数据就没了）：{parts}")
    a("")
    a("## 提醒建议")
    a(f"- 方式：**{rep['reminder']['mode']}**")
    a(f"- 理由：{rep['reminder']['why']}")
    if rep["reminder"].get("frequency"):
        a(f"- 频率：{rep['reminder']['frequency']}")
        a("- 配置：见 references/reminders.md（用 automation 工具建，不要手写 cron 字符串）")
    return "\n".join(L)


def cmd_ready(args) -> int:
    """发布就绪检查：全绿才让本 skill 自作主张发布，任一条不过就回去问用户。"""
    ws = workspace(args.workspace)
    plan = load_json(ws / "plan.json")
    if plan is None:
        die(f"没找到 {ws / 'plan.json'}。先跑：python3 plan.py init --workspace {ws}")

    checks: list[dict] = []
    warns: list[str] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    policy = str(plan.get("publish_policy") or "confirm").strip().lower()
    paused = bool(plan.get("paused"))
    add("计划未停更", not paused, "plan.json 的 paused=true" if paused else "")
    add("已授权自动发布", policy == "auto",
        "" if policy == "auto" else f'publish_policy={policy}，要自动发布得改成 "auto"')

    post: Path | None = None
    if args.post:
        post = Path(args.post).expanduser().resolve()
        add("post 目录存在", post.is_dir(), str(post))
    else:
        ready, _ = classify_posts(ws)
        if len(ready) == 1:
            post = ws / "posts" / ready[0]["name"]
            add("成品唯一", True, f"posts/{ready[0]['name']}")
        else:
            add("成品唯一", False,
                f"posts/ 下有 {len(ready)} 篇可直接发，得用 --post 指定发哪一篇")

    tags_n = 0
    if post is not None and post.is_dir():
        title = (post / "title.txt").read_text(encoding="utf-8").strip() \
            if (post / "title.txt").is_file() else ""
        body = (post / "body.txt").read_text(encoding="utf-8").strip() \
            if (post / "body.txt").is_file() else ""
        images = [f for f in post.iterdir()
                  if f.is_file() and f.suffix.lower() in IMAGE_EXT]
        missing = [n for n, ok in (("manifest.json", (post / "manifest.json").is_file()),
                                   ("标题", bool(title)), ("正文", bool(body)),
                                   ("配图", bool(images))) if not ok]
        add("交付物齐全", not missing, "缺 " + "、".join(missing) if missing else
            f"{len(images)} 张图")
        add(f"标题 ≤ {TITLE_MAX} 字", 0 < len(title) <= TITLE_MAX, f"{len(title)} 字")
        if len(body) > BODY_MAX:
            warns.append(f"正文 {len(body)} 字，超过 {BODY_MAX} 字上限（平台只警告不拦）")
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        last = lines[-1] if lines else ""
        tags = [t for t in last.split() if t.startswith("#")]
        tags_n = len(tags)
        add("末行是标签行", bool(tags) and len(" ".join(tags)) == len(last),
            f"{tags_n} 个标签" if tags else "正文最后一行不是以 # 开头的标签")
        ok, why = run_render_check(post, find_render_cards())
        add("版式实测全绿", ok, why)
        ok, why = run_content_check(post, find_check_content())
        add("内容合规（指令遵循）", ok, why)

    state_path = occupancy_path(args, ws)
    state_q = ((load_json(state_path) or {}) if state_path else {}).get("queue") or {}
    scheduled = state_q.get("scheduled") or []
    # 队列扫描不完整时，**不能**报"有空闲时段"：漏读的那几条正是被占用的时段，
    # 照着报出来的空档排期就会把新笔记压在旧笔记上面。宁可判不过。
    #
    # 唯一的例外：调用方明确交来一份**人工核对过的**占用清单（--scheduled-verified）。
    # 扫描会漏读，人不会——那时以这份清单为准，不再看 partial。
    if getattr(args, "scheduled_verified", None):
        vf = Path(args.scheduled_verified).expanduser()
        v = load_json(vf) or {}
        scheduled = v.get("scheduled") or []
        add("占用清单有人工来源", bool(scheduled) and bool(v.get("source")),
            f"{len(scheduled)} 条 —— {v.get('source') or '缺 source 字段'}")
    elif state_q.get("partial"):
        add("队列扫描完整", False,
            f"只读到 {state_q.get('cards_scanned')} / {state_q.get('expected_total')} 篇，"
            "空档不可信——先把 Chrome 切到前台重跑 xhs_state.py")
    elif not scheduled:
        # 没有占用清单时**不能**报空档：`scheduled` 是空的，任何时段都会显得空闲，
        # 于是把当天 22:00 这种早就被占的档报成"下一个空档"。宁可判不过。
        add("占用清单可用", False,
            f"没有读到任何已定时的档位（{state_path or '未提供 --state'}）——"
            "先跑 xhs_state.py queue，或用 --scheduled-verified 交一份人工清单")
    cad = plan.get("cadence") or {}
    now = datetime.now()
    weekdays = cad.get("weekdays") or [2, 6]
    slots_txt = cad.get("slots") or ["20:00"]
    # 一天两个时段时"发最近的那个"是**自动成立**的：`iter_slots` 把每天都时段展开后
    # 排序，所以 `slots: ["12:30", "22:00"]` 就等于"先到先得"——今天 22:00 被占了，
    # 下一个候选自然是明天 12:30，而不是 22:00 线一路排到窗口外（2026-10-09 用户规则）。
    slot = next_free_slot(now, args.weeks, weekdays, slots_txt, scheduled)
    # 排得进去 ≠ 排得上：平台只收窗口内的时刻，超窗会被悄悄换成别的时间。
    window_end = now + timedelta(days=SCHEDULE_WINDOW_DAYS)
    add("有空闲时段", slot is not None,
        slot.strftime("%Y-%m-%d %H:%M") if slot else f"未来 {args.weeks} 周排满了")
    if slot is not None:
        add("空档在平台定时窗口内", slot <= window_end,
            f"{slot:%Y-%m-%d %H:%M}"
            if slot <= window_end else
            f"{slot:%Y-%m-%d %H:%M} 超出平台窗口（今天 + {SCHEDULE_WINDOW_DAYS} 天，"
            f"上限 {window_end:%Y-%m-%d}）——别排：平台会把超窗时间悄悄退回默认值。"
            "窗口内的白天时段会在 slots 里被自动选中；若 slots 只有一档，"
            "先按「一天两个时段」在 plan.json 的 cadence.slots 里补一档")

    passed = all(c["ok"] for c in checks)
    rep = {"generated_at": f"{datetime.now():%Y-%m-%d %H:%M}", "workspace": str(ws),
           "post": str(post) if post else None, "tags": tags_n,
           "next_slot": slot.strftime("%Y-%m-%d %H:%M") if slot else None,
           "ready": passed, "checks": checks, "warnings": warns}

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"# 发布就绪检查 {rep['generated_at']}")
        print("")
        for c in checks:
            print(f"- [{'x' if c['ok'] else ' '}] {c['name']}"
                  + (f" —— {c['detail']}" if c["detail"] else ""))
        for w in warns:
            print(f"- [x] (提醒) {w}")
        print("")
        if passed:
            print(f"结论：**可自动发布**，下一个空档 {rep['next_slot']}。")
        else:
            bad = "、".join(c["name"] for c in checks if not c["ok"])
            print(f"结论：**未通过，回到确认闸门**。没过：{bad}")
    return 0 if passed else 1


def cmd_report(args) -> int:
    ws = workspace(args.workspace)
    plan = load_json(ws / "plan.json")
    if plan is None:
        die(f"没找到 {ws / 'plan.json'}。先跑：python3 plan.py init --workspace {ws}")
    cad = plan.get("cadence") or {}
    ppw = float(cad.get("posts_per_week") or 0)
    weekdays = cad.get("weekdays") or [2, 6]
    slots_txt = cad.get("slots") or ["20:00"]
    targets = {**DEFAULT_TARGETS, **(plan.get("targets") or {})}

    ready, semi = classify_posts(ws)
    topics_open, topics_done = count_topics(ws)

    state = load_json(Path(args.state).expanduser()) if args.state else None
    scheduled = ((state or {}).get("queue") or {}).get("scheduled") or []
    drafts = ((state or {}).get("drafts") or {})
    hist = ((state or {}).get("history") or {}).get("published") or []

    now = datetime.now()
    paused = bool(plan.get("paused"))
    slots = [] if paused else iter_slots(now, args.weeks, weekdays, slots_txt)
    rows, unmatched = build_schedule(slots, scheduled, ready)
    for u in unmatched:
        at = parse_post_time(u.get("time"))
        if at is not None:
            rows.append({"at": at, "source": "off-plan", "title": u.get("title"),
                         "noteId": u.get("noteId")})
    rows.sort(key=lambda r: r["at"])

    buffer_weeks = (len(ready) / ppw) if ppw else 0.0
    target_ready = ppw * targets["ready_weeks"]
    empty = sum(1 for r in rows if r["source"] is None)
    to_target = max(0, int(target_ready - len(ready) + 0.999))

    timing = None
    if hist:
        buckets, too_fresh = hour_buckets(hist)
        usable = sum(b["n"] for b in buckets)
        timing = {"total": len(hist), "usable": usable, "too_fresh": too_fresh,
                  "buckets": buckets,
                  "thin": usable < 12 or (buckets and max(b["n"] for b in buckets) < 3)}

    rep = {
        "generated_at": f"{now:%Y-%m-%d %H:%M}",
        "workspace": str(ws),
        "cadence": {"posts_per_week": ppw, "weekdays": weekdays, "slots": slots_txt},
        "targets": targets,
        "inventory": {
            "ready_count": len(ready), "semi_count": len(semi),
            "topics_open": topics_open, "topics_done": topics_done,
            "target_ready": target_ready,
            "target_semi": ppw * targets["semi_weeks"],
            "target_topics": ppw * targets["topic_weeks"],
            "ready": ready, "semi": semi,
        },
        "coverage": {"weeks": buffer_weeks,
                     "horizon_weeks": args.weeks},
        "horizon_weeks": args.weeks,
        "schedule": [{**r, "at": r["at"].isoformat()} for r in rows],
        "gap": {"empty_slots": empty, "to_target": to_target,
                "horizon_weeks": args.weeks},
        "unmatched_scheduled": unmatched,
        "timing": timing,
        "platform": None,
        "paused": paused,
        "reminder": ({"mode": "ad-hoc",
                      "why": "计划处于停更状态（plan.json 的 paused=true），排期与提醒都停。"}
                     if paused else
                     reminder_advice(buffer_weeks, ppw, targets["ready_weeks"])),
    }
    if state:
        q = state.get("queue") or {}
        rep["platform"] = {
            "scheduled_count": len(scheduled),
            "in_review_count": (q.get("in_review_count") or 0),
            "draft_counts": drafts.get("counts"),
            "partial": bool(q.get("partial")),
            "cards_scanned": q.get("cards_scanned"),
            "expected_total": q.get("expected_total"),
        }

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(render_text(rep))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="小红书发布排期计算")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="建 workspace 与 plan.json")
    p.add_argument("--workspace", default=None)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("ready", help="发布就绪检查：全绿才可自动发布")
    p.add_argument("--workspace", default=None)
    p.add_argument("--post", default=None, help="post 目录；不给则要求 posts/ 下只有一篇成品")
    p.add_argument("--state", default=None, help="xhs_state.py 输出，用来避开已定时档位")
    p.add_argument("--scheduled-verified", default=None,
                   help="人工核对过的占用清单 JSON"
                        "（{\"source\":\"…\",\"scheduled\":[{\"time\":\"2026-10-07 22:00\"}]}）；"
                        "给了它就不再依赖扫描，也不再因 partial 判不过")
    p.add_argument("--weeks", type=int, default=4)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_ready)
    p = sub.add_parser("report", help="出排期与缺口报告")
    p.add_argument("--workspace", default=None)
    p.add_argument("--state", default=None, help="xhs_state.py 输出的 JSON")
    p.add_argument("--scheduled-verified", default=None,
                   help="人工核对过的占用清单 JSON"
                        "（{\"source\":\"…\",\"scheduled\":[{\"time\":\"2026-10-07 22:00\"}]}）；"
                        "给了它就不再依赖扫描，也不再因 partial 判不过")
    p.add_argument("--weeks", type=int, default=4)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
