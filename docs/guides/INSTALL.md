# 安装

仓库：<https://github.com/Ewen2015/reading-pkm>

把九个 skill 装进你所用的 agent。日常用软链：改仓库立即生效，书库留在原处。
整包 plugin 安装只用于分发（见第 6 节），不要拿它做日常安装。

## 1. 取得仓库

```bash
git clone https://github.com/Ewen2015/reading-pkm.git
cd reading-pkm
```

需要 `git` 和 Python 3.9+。核心脚本只用标准库；`book-fetch` 的 PDF 文字层探测
可选装 PyMuPDF 或 poppler，缺了会如实报「未探测」。

## 2. 软链九个 skill

目录名就是 skill 名。把 `skills/` 下的九个目录软链到 agent 的 skills 根：

```bash
SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"   # 其他 agent 换成自己的 skills 根
mkdir -p "$SKILLS_DIR"
for s in book-fetch read-a-book second-brain scandinavian-style \
         rednote-post rednote-assistant rednote-reply human-writing rsi-reading; do
  ln -sfn "$PWD/skills/$s" "$SKILLS_DIR/$s"
done
```

同名目录已经存在时，先删掉或改名——`ln -sfn` 不会覆盖真目录。

- Codex：`${CODEX_HOME:-$HOME/.codex}/skills/`
- 其他 agent：按各自的约定；常见的是 `~/.agents/skills/`
- 不能软链（如 Windows）：把 `ln -sfn` 换成 `cp -R`，代价是改仓库后要重跑一遍

软链让 agent 看到的就是仓库里的这份代码，`git pull` 后立即生效。

## 3. 验证

```bash
python3 skills/second-brain/scripts/brain.py check
```

它应该找到仓库里的 `library/`，并报出「N 本卡片，全部通过」（后面可能跟几条提醒）。
找不到库时，命令会打印它查过的路径。

## 4. 书库放哪

默认就是仓库里的 `library/`，skill 从自己的兄弟目录找得到，不用设任何东西。
查找顺序：`$SECOND_BRAIN_HOME` → 兄弟目录 `library/` → `~/.local/share/second-brain`。

书库搬到仓库外（网盘、另一台机器）时：

```bash
export SECOND_BRAIN_HOME="/path/to/library"
```

## 5. 两个 skill 需要额外配置

配置都在仓库外——**仓库里不含任何凭据或书源信息**：

- `book-fetch`：书源适配器，见 [`../../skills/book-fetch/SKILL.md`](../../skills/book-fetch/SKILL.md)
- `rednote-post`：浏览器桥接与扩展，见 [`../../skills/rednote-post/references/publishing.md`](../../skills/rednote-post/references/publishing.md)

## 6. 分发：整包安装（可选）

仓库本身是一个 Codex plugin，`.agents/plugins/marketplace.json` 只列这一个 plugin：

```bash
codex plugin marketplace add /path/to/reading-pkm
codex plugin add reading-pkm@reading-pkm
```

装出来的 skill 带 `reading-pkm:` 前缀。**但它是复制，不是引用**：会把插件目录
（含 `library/`）复制进 Codex 的缓存，之后读到的是安装那一刻的快照。日常别用；
用它分发时，把 `SECOND_BRAIN_HOME` 指回活的仓库。

## 7. 卸载

删掉软链即可，仓库和书库都不动：

```bash
SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
for s in book-fetch read-a-book second-brain scandinavian-style \
         rednote-post rednote-assistant rednote-reply human-writing rsi-reading; do
  rm -f "$SKILLS_DIR/$s"
done
```
