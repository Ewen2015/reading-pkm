---
name: rednote-post
description: >-
  把一份素材（读书笔记、产品观察、教程、观点）做成小红书图文笔记并发到用户自己的账号：
  写标题与正文、生成 1080×1440 卡片图、本地实测排版、经用户确认后用浏览器桥接发布、发布后核验。
  当用户说"发小红书""发一篇笔记""rednote""做图文 post""把这份笔记发出去""配图发小红书"时激活。
  不适用于：只要文案不要发布、只要图片不要发布、无人值守或批量发布、
  发布到用户自己账号以外的位置（品牌号代运营、投流、多账号矩阵）。
metadata:
  short-description: 生成并发布小红书图文笔记（发布前确认闸门，可被 rednote-assistant 授权替代）
---

# 小红书图文笔记

一条流水线：**文案 → 卡片图 → 实测 → 用户确认 → 发布 → 发布后核验**。
其中「用户确认」这一步，在 `rednote-assistant` 已获得发布授权（`publish_policy: "auto"`
且它的就绪检查全绿）时由该检查代替。

下面命令里的 `<skill>` 指本文件所在目录
（通常是 `${CODEX_HOME:-$HOME/.codex}/skills/rednote-post`）。
按字面写 `scripts/xhs.py` 会找不到——当前工作目录通常是用户的项目，不是 skill 目录。

## 三条铁律

1. **发布前必须拿到明确同意。** 把标题、**完整正文**、**每一张图**、合集、可见范围一次性摆给
   用户看，然后**停下等待**。用户没点头不许点发布；用户改了任何一项，重新摆一遍再问。
   **唯一的例外**：调用方是 `rednote-assistant`，且它的 `plan.py ready` 已全绿
   （`plan.json` 里 `publish_policy: "auto"`）——那条授权等同于用户的同意，不必再重复确认。
   例外只覆盖"发不发、发在哪一档"；改标题、正文、图片本身仍然要问。
2. **发布是公开且难以撤回的动作。** 不替用户决定发什么、不自动补发、不批量发。
   账号／合集／可见范围以用户**当次**的指示为准，不沿用上一次。
3. **图片必须实测。** 渲染完看一眼不算数——用 `<skill>/scripts/render_cards.py` 量字号下限、溢出、
   页脚碰撞和孤字换行。曾有一版卡片因为辅助函数把每张卡自己的 CSS 丢掉了，全部以浏览器默认
   16px 渲染，肉眼看只觉得"字有点小"，看不出根因。

## 工作流

### 1. 定角度

**先读 [`references/persona.md`](references/persona.md)——当前博主的人设，然后才开始想角度。**
它是一份**输入，不是锁**：里面写清了这个号是谁、语气在哪儿、哪些写法是这个号的定式
（以及哪些只是可以摆动的）。用户当次指定了别的语气，就以用户说的为准；
换了账号，就换这份文件。

能从上下文推断就别问。要确定的是：发什么、给谁看、是否指定合集、有没有现成图片。
选题时挑**对读者直接有用**的点——读者点赞收藏的是"这跟我有关"，不是"这个内容很好"。
文案结构与体量见 [`references/copywriting.md`](references/copywriting.md)。

**定角度时写两句话**（数据支持，见
[`references/copywriting.md`](references/copywriting.md#本账号实测角度决定收藏2026-10-11)）：
① 读者此刻在经历的具体毛病是什么；② 书里给它命名的机制是什么。
两句都写不出来就别写文案——那是收藏率最低的一类选题。

### 2. 写标题与正文

> **先调 [`human-writing`](../human-writing/SKILL.md)，把
> [`references/persona.md`](references/persona.md) 当人设传进去，再动笔。**
> "没有 AI 味"和"贴这个人设"两件事由它负责（连接词／行话／排比三连／每段一句金句／
> 匀称到没有起伏——以及先读人设、抽声音指纹、写完出声念）。本 skill 只在其上加平台约束：
> 标题 ≤ 20 字、正文 ≤ 1000 字、重音只能用 emoji／断行／符号、末行单独放 `#标签`。

**不许编故事。** 开头不编"一个朋友跟我说……""上个月我……"这种桥段——具体必须是真的：
书里写的（例子／数字／引文，能落页码）、公开可查的事实、或者读者的处境（用「你」直接说）。
读者自己的处境本来就是本号收藏率最高的一档（见 `references/copywriting.md` 的实测表），
不用绕一个朋友进来。真写了第一人称的真实素材，在 `manifest.json` 里写一句 `real_material`
说来路；`check_content.py` 会查这一条。

**按人设写。** 语气、重音手法（emoji／断行／符号，正文不能加粗）、"每条观点接一句怎么用"、
结尾回到读者身上、单独一行的出处行——都在
[`references/persona.md`](references/persona.md) 的第 3 节里，每条附了成品里的证据。
写完对着它的第 6 节自查一遍。

标题 ≤ 20 字（超限 CLI 直接报错）；正文 ≤ 1000 字。正文末尾的 `#标签` 会被 CLI 自动识别成
话题。**正文最后一行单独放 `#标签`，用空格分隔**（如 `#意会认知 #波兰尼 #读书`），
不要另填 `--tags`。

**正文不能加粗**——平台不支持富文本，重音只能用 emoji／断行／符号，写法见
[`references/copywriting.md`](references/copywriting.md#正文没有加粗重音只能用这三招)，
别去试 `**字**`。

**成品里不许出现"待核实／需查证"这类半成品标记**（正文和卡片都算）。能核实就当场核实，
核不了就把断言收缩成"这本书说……"，再不然整条删掉。发布前跑一次机械自检，见
[`references/copywriting.md`](references/copywriting.md#成品里不许出现半成品标记)。

> 标签必须逐个从联想下拉里用**真点击**选中才会变成话题；上游 CLI 用的是合成 JS 点击，
> tiptap 会忽略，结果是只有最后一个标签生效。`fill` 已经绕开这条坏路径自己做，见
> [`references/publishing.md`](references/publishing.md#话题标签必须用真点击这是最容易踩的坑)。

### 3. 做卡片图
用 [`assets/card.css`](assets/card.css) 的令牌与版式起步，从
[`assets/cover.html`](assets/cover.html) 和 [`assets/card.html`](assets/card.html) 改内容。
尺寸固定 1080×1440（3:4，小红书信息流原生比例）。字号下限、卡片类型和素材处理见
[`references/cards.md`](references/cards.md)。

**强调色用 Volvo Safety Orange `#FD6408`（`--accent`），不要用绿色**；它是信号色，
只点短句与关键词，成段正文走 `--ink2`（橙色在暖纸底上只有约 2.6:1 对比度）。

**源素材要转成 RGB PNG 并确认方向**——手机拍的 HEIC 常带 EXIF 旋转，直接 `Image.open()` 会歪。

**封面的主体图必须是这本书真实存在的封面，中文版优先。** 两种都行——
**实体书照片**，或**电子封面图**（出版社设计的那张封面本身）；
**不行的是自己拿书名排出来的合成图和星空／剪影这类意象图**，读者认不出这是哪本书。
判据只有一句：缩到巴掌大时，读者认不认得出这本书。

封面同时也是本号**唯一被平台数据判"不及格"的一环**（同类诊断里封面点击率 3/3 低于中位数，
而内容丰富度是同类前 1%）——所以每版封面要在 `manifest.json` 写一行 `cover.hypothesis`，
说明这一版赌哪个属性，**攒到 6 篇再下结论**。见
[`references/cards.md`](references/cards.md#本账号实测封面是唯一被平台判有待提升的一环2026-10-11)。

手边有实物就自己拍（自带"我真读过"的证据，最不像广告）；没有实物就用电子封面，
但要靠版式做"软"——铺在暖纸底上、压一点饱和、加一层柔和阴影，别做成电商白底商品图；
带水印或促销角标的一律不能用。实拍先用 [`scripts/prep_cover.py`](scripts/prep_cover.py) 摊平
（`grid` 读四角 → `flat` 校正），电子封面只需**转 RGB PNG 并确认宽度 ≥1600px**。
处理完**贴给用户看一眼**再排版。选材判据与退路见
[`references/cards.md`](references/cards.md#封面主体素材真实封面优先实拍照片或电子封面都行)。

封面**每本都带**一层**主题插画装饰**：书封**原版完整**地摆着，装饰画在它四周的两栏里
（矢量线稿，由 [`scripts/cover_deco.py`](scripts/cover_deco.py) 生成）。
**装饰的主题必须从这本书自己拆出来**（2–3 个 motif），不是套现成的一版——`garland` / `notes`
两个预设都是《丈夫之美》那一版，只当底版参考，直接照搬会被判红。
拆出来的 motif 写进 `manifest.json` 的 `cover.deco.motifs`，`check_content.py` 会查
「有没有、贴全没有、声明没有、**是不是别篇那一张**」四项。做法与三条硬规矩见
[`references/cards.md`](references/cards.md#封面改造书封原版展示主题装饰画在四周)。

### 4. 本地实测

```bash
python3 <skill>/scripts/render_cards.py <post-dir>
```

它渲染 `manifest.json` 里列的每张卡，并检查：溢出、压页脚、正文区字号低于下限、孤字换行。
**任何一项不过就改版式，不要靠肉眼放行。** 修完重跑，直到全绿再进入下一步。

排版之外还有一层**内容合规**，用另一个脚本查：

```bash
python3 <skill>/scripts/check_content.py <post-dir>
```

它查的是 `render_cards.py` 看不见的"意思"——半成品标记、正文加粗、强调色 `#FD6408`、
封面素材是否可用、有没有并存的第二版本文案、**正文有没有编出来的故事**、封面装饰层。规则写在
[`references/copywriting.md`](references/copywriting.md) 和
[`references/cards.md`](references/cards.md) 里，但**判据落在脚本里**，
不靠每次重读 SKILL.md 去记。两个脚本都退出码 0 才算这一步过。

其中「人设：无 AI 味词」那一项的词表来自 `human-writing`，不是本 skill 自抄的
（`check_content.py` 直接 import 它的 `human_check.py`，并读
[`references/persona.md`](references/persona.md) 里那行机器可读的「禁忌词」）。
想要更细的**软信号**——排比三连、段落长度过于均匀——在出声念之前再跑一次：

```bash
python3 <human-writing>/scripts/human_check.py <post-dir>/body.txt \
    --persona <skill>/references/persona.md --strict
```

**加 `--strict`**：软信号（`→` 行动行过多、同一个 emoji 领起多段、破折号过密、
每段一样长）也判失败。"每条观点都配一句'所以怎么用'"这种模板化写法，只有它能拦住。

### 5. 用户确认（闸门）

把下面这些一次性摆出来，然后停：

- 标题
- **完整正文**（不要摘要，用户要逐字看）
- **每一张图**（用绝对路径的 Markdown 图片，让它在对话里直接显示）
- 合集、可见范围、原创声明状态

问一句明确的"确认发布吗"。**这一步不许跳过，也不许合并进上一步。**

例外见铁律 1：由 `rednote-assistant` 调用、且它的发布就绪检查全绿时，这一步视为已通过。
自己直接接到用户请求时，没有这个例外。

### 6. 填表并回读核验

```bash
python3 <skill>/scripts/xhs.py fill   --dir <post-dir> --collection "<合集名>"
python3 <skill>/scripts/xhs.py verify --dir <post-dir> --collection "<合集名>"
```

`fill` 从 `<post-dir>/manifest.json` 取图片顺序，所以目录里有别的图也不会传错。

`verify` 从页面回读标题、正文长度、图片张数和合集名，并和本地文件比对。
**不要相信 `fill` 的成功输出**——它是"我以为填进去了"，`verify` 才是页面上的事实。
其中**话题标签数**（`[role=textbox] .tiptap-topic`）必须等于正文末尾的标签个数；
数量对不上就说明有标签退化成了普通文字。

标签没上去（改了标签、或想补几个）可以单独重跑，不用重新填整张表：

```bash
python3 <skill>/scripts/xhs.py tags --dir <post-dir>
```

要**原创声明**或**定时发布**，在 `fill` 之后、`publish` 之前补这两步（都幂等，可重跑）：

```bash
python3 <skill>/scripts/xhs.py original                      # 打开原创声明，并过「原创声明须知」弹窗
python3 <skill>/scripts/xhs.py schedule "2026-10-06 22:00"   # 打开定时发布并写入时间
```

两个开关都是自定义组件，脚本自己做了真点击与回读；`schedule` 会校验时间晚于当前时间
30 分钟以上。定时发布打开后，发布按钮的文字会变成「定时发布」。

**定时发布有窗口上限（实测：今天 + 13 天），而且超窗不报错。** `schedule` 的回读读的是
表单输入框（永远显示你填的时刻），平台存下的却可能是它自己的默认值。所以提交之后
**必须回读卡片**，不能只看表单：

```bash
python3 <skill>/scripts/xhs.py verify-scheduled --title "<标题片段>" --expect "2026-10-21 22:00"
```

对不上就把这条删掉（`delete-note`），成品留存、等窗口放开再排。窗口判据与细节见
[`references/publishing.md`](references/publishing.md)。

### 7. 发布

```bash
python3 <skill>/scripts/xhs.py publish
```

点完按钮 CLI 可能报"未捕获到发布反馈"，这是**正常的模糊结果**，不代表失败。以页面状态为准：
URL 出现 `published=true`（定时发布则是 `/publish/success`）且表单被清空 = 已提交。

### 8. 发布后核验

```bash
python3 <skill>/scripts/xhs.py check-published "<标题>"
```

定时发布的笔记**不在「已发布」页签里**——时间没到之前它出现在「全部」，带「定时发布」标记。
要连时间一起核验，用 `rednote-assistant` 的 `xhs_state.py queue` 扫定时队列。

到 `creator.xiaohongshu.com/new/note-manager` 确认：出现在**已发布**、不在**审核中**。
把结论告诉用户（已发布／审核中／未找到），不要只报"发布完成"。

## 修订循环

用户几乎一定会改文案或图片。改完的固定动作是：

1. 重新渲染 + 重跑 `render_cards.py`；
2. **重新导航到干净的发布页**（`xhs.py reset`），再 `fill`——否则旧图会残留，
   图片会叠加成 10 张；
3. 重跑 `verify`，重新摆给用户确认。

## post 目录约定

一个笔记一个目录，固定这几个名字，脚本就能直接跑：

```
<post-dir>/
|-- manifest.json      图片清单与渲染参数（render_cards.py 读）；建议带 cover.source 声明封面出处
|-- title.txt          标题，≤20 字
|-- body.txt           正文，≤1000 字
|-- build/             卡片 HTML + 复制的 card.css
`-- assets/            原图、处理后的素材
```

`body.txt` 只保留一个当前版本。**不要**留 `body-v2.txt` 这种并存版本——
`fill` 默认读 `body.txt`，改完忘了同步就会发出旧文案。

## 环境

发布依赖浏览器桥接（Chrome 扩展 + 本地 WebSocket 服务）。首次配置、选择器、故障排查见
[`references/publishing.md`](references/publishing.md)。跑之前先确认桥接就绪：

```bash
python3 <skill>/scripts/xhs.py status
```

`status` 不通就先按 `references/publishing.md` 里的 `setup_bridge.sh` 配好，**不要**在桥接
未就绪时硬撞 `fill`。

## 参考文件

- [`references/persona.md`](references/persona.md)——**当前博主的人设**（账号事实、十五条写法定式、
  可摆动的部分、反面清单、写完自查、怎么更新）
- [`references/copywriting.md`](references/copywriting.md)——标题公式、正文骨架、字数与标签规则
- [`references/cards.md`](references/cards.md)——1080×1440 卡片系统、字号下限、素材处理
- [`references/publishing.md`](references/publishing.md)——桥接环境、选择器、故障排查、发布后核验
- [`scripts/prep_cover.py`](scripts/prep_cover.py)——把手机拍的实体书封面摊平成卡片可用图（`grid` / `flat`）
- [`scripts/analytics.py`](scripts/analytics.py)——读平台数据看板：`fetch` 落快照、`report` 出表
  （曝光／封面点击率／收藏率／同类诊断／观看来源）。定角度和做封面前先看一眼最新快照
- [`../human-writing/SKILL.md`](../human-writing/SKILL.md)——**写这一步调它**：去 AI 味 +
  贴人设（声音指纹、人设文件合约、可以直接跑的 `human_check.py`）
