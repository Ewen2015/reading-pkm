# reading-pkm

> 把读过的书，变成下次思考时**会被调用**的视角。

一条个人阅读的知识管理流水线：**九个 [Codex](https://developers.openai.com/codex/) skill 打成一个
Codex plugin**（`.codex-plugin/plugin.json`），装一次全部就位，也可以只把某一个 skill 拷走用。
它把 Harold Jarche 的 **Seek › Sense › Share** 模型落成能跑的东西：能被自动化的交给机器，
不能被自动化的——判断与品味——留在人这里。

![Seek › Sense › Share 落到一条可执行的流水线上](docs/framework.png)

## 框架：Seek › Sense › Share

Harold Jarche 在 2014 年的 [Sense-making and sharing](https://jarche.com/2014/07/sense-making-and-sharing/)
里，把个人知识管理（PKM）压成三个动作：**seek**（持续找新知识、找不同的人）、**sense**（把它变成
自己的）、**share**（还给网络）。他真正想说的不是这三个词，而是**失衡的代价**：

| 只做其中一件 | 会怎样 |
| --- | --- |
| 只 Seek | 信息过眼不过脑，自己的成长不会发生 |
| 只 Sense | 你不会成为任何知识网络里的节点，错过和他人一起学 |
| 只 Share | 制造噪音，别人开始忽略你 |
| 三件都做，且平衡 | 这才叫 PKM |

平衡没有标准答案，Jarche 的原话是 *each person must find his or her own way*。他还说，
PKM 最难的部分是找到一个**能长期坚持的 sense-making 习惯**；他自己选的是写博客，理由很实在——
博客是自己的，**所有权让人坚持得下去**。

这个仓库是 Ewen 的答案。

## AI 把哪一段变便宜了

AI 对这条链的冲击是不均匀的：

- **Sense 变便宜了。** 三层笔记、脉络梳理、卡片化、带出处地答问——过去要一个下午，现在几分钟。
- **Share 变便宜了。** 排版、渲染、发布、回读核验都能自动跑，边际成本接近零。
- **Seek 没有变便宜。** "这本书值不值得我读"，没有任何模型能替你回答。

所以：**当 sense 和 share 的边际成本趋近于零，瓶颈和护城河就都只剩下 Seek。**
这个仓库的分工就是照这句话切的——机器把两端做厚，人守住起点。

一个诚实的补充：这条链**还没有闭环**。各渠道的数据回来了（小红书有阅读与收藏，第二大脑有被
引用），但还没有任何下游在读它，选题与呈现仍靠手感。`rsi-reading` 就是用来量这条环、
一次只改一处的。

## 三段各自怎么实现

### SEEK · 寻求 —— 决定权在人

| Skill | 做什么 |
| --- | --- |
| [`book-fetch`](./skills/book-fetch) | 把「我要读某本书」变成「手上有一个能直接做笔记的电子书文件」：检索候选、剔除李鬼（摘编／学习指南）、六维综合评分、解析封面与书目核对，**把格式和封面摆给用户确认之后**才下载，下载完洗掉文件名里的来源后缀并登记进待读队列。书源适配器配置在仓库外——**仓库本身不含任何书源信息**。 |
| [`read-a-book`](./skills/read-a-book) 的检视阅读 | 任何一本书的第一步：几分钟建一张地图，回答"这本书在谈什么、值不值得细读"。 |

这两个 skill 都**不替用户做决定**：`book-fetch` 只把候选和证据摆好，`read-a-book` 一律从检视阅读开始。

### SENSE · 化用 —— 机器整理，人下判断

| Skill | 做什么 | 示例 |
| --- | --- | --- |
| [`read-a-book`](./skills/read-a-book) | 用《如何阅读一本书》(Adler & Van Doren) 的方法执行真实阅读：检视 → 分析 → 主题，每本书都梳理来龙去脉（写作目的、所属思潮、影响它的与它影响的），产出结构化笔记；可按需压成**一页纸（one-pager）**。 | <img src="docs/skills/read-a-book.png" width="220" alt="《思考的框架》三册一页纸读书笔记：三栏分别放这本书在回答什么、三册结构与 48 个模型、上游与下游的隐身人"> |
| [`second-brain`](./skills/second-brain) | 用已经读过的书回答一个具体问题：挑出真正相关的 1–2 本，用它们的思想视角参与思考，每个观点都带出处与作者。**交付的是意见，不是读书报告。** 书库按「index 当注册表、卡片按需加载」组织——一次问答约 3–5K tokens，而不是把整个书库塞进上下文。 | — |
| [`scandinavian-style`](./skills/scandinavian-style) | 斯堪的纳维亚风格的设计系统：16:9 版式网格、字号层级、页脚规范、配色与图表规则，附可直接改的单页 HTML 模板，以及**密集单页（one-pager）**三栏模式与实测校验脚本。不带任何品牌资产。 | <img src="docs/skills/scandinavian-style.png" width="220" alt="16:9 单页模板渲染结果：章节标签、32pt 标题、正文与 5 节点时间线，右侧边栏列表，底部四元素页脚"> |

书库里是每本书一张卡、一份原笔记，都是纯 markdown，随读随加。

### SHARE · 分享 —— 同一份素材，多种 UI

Jarche 提醒 share 要讲分寸：**知道什么时候、对谁分享**，才能建立信任、保持好的信噪比。
技术上的对应是把「内容」「载体」「渠道」拆开——小红书只是其中一种 UI 与渠道，不是终点。

| Skill | 做什么 | 示例 |
| --- | --- | --- |
| [`rednote-post`](./skills/rednote-post) | 把一份素材做成小红书图文笔记并发到自己的账号：写标题与正文、生成 1080×1440 卡片图、**实测**排版（字号下限／溢出／压页脚／孤字换行）、经用户确认后用浏览器桥接发布并回读核验。含卡片模板、渲染校验脚本与发布 CLI。 | <img src="docs/skills/rednote-post.png" width="220" alt="小红书图文的封面与两张内页卡片：你不是不够聪明，是手里的模型太少"> |
| [`rednote-assistant`](./skills/rednote-assistant) | 小红书发布排期助理：定更新频率与发布时段、看平台上有没有未来定时发布的笔记、盘草稿箱、算现有成品能撑几周、指出排期缺口、按账号自己的历史数据挑最佳发布时间。**只排期，不写不发**——文案与发布走 `rednote-post`。 | <img src="docs/skills/rednote-assistant.png" width="220" alt="排期体检报告：每周 2 篇的计划下，未来 3 周排期表标出 5 个空档与已定时的笔记，并给出素材积累量目标与提醒建议"> |
| [`rednote-reply`](./skills/rednote-reply) | 互动：读私信（聊天）会话，按账号人设拟一句回话，**经用户逐条确认后再发**并回读核验。默认只读，`send` 不加 `--yes` 只 dry-run。人设单独一份（`references/soul.md`），聊天口径与写笔记那份分开。评论入口尚未打通。 | — |
| [`human-writing`](./skills/human-writing) | 写作层：把稿子改写成"人话"——去掉 AI 味（连接词／行话／排比三连／每段一句金句／匀称到没有起伏），再落到一份**给定的人设**上（先读人设、抽声音指纹、写完出声念）。**不绑定账号**：调用方传自己的人设文件，`rednote-post` 是第一个调用方。含可直接跑的 AI 味机判脚本（`rednote-post` 的 `check_content.py` 直接复用它，不再自抄词表）。 | — |

### 之上的一层

| Skill | 做什么 |
| --- | --- |
| [`rsi-reading`](./skills/rsi-reading) | 把「读书」这条链当成闭环做递归自我改进：度量 read-a-book → 沉淀 → 渲染与分发 → second-brain 取用 的流水线，找断点、给改进项排优先级、跑一次只改一个变量的最小试验并记账。分发按「内容模型／载体／渠道」三层拆开——**小红书只是其中一种 UI 与渠道**。附只读观测仪（`rsi.py`）与三份活文档。**它不读书、不写笔记、不发帖**——改的是流程本身。 |

## 留在 Ewen 的那一步：读书的审美

三段里只有 Seek 是 100% 留给人做的。原因不是技术不够，而是**这一段的判断本质上是审美，不是检索**。

Adler 在《如何阅读一本书》里给过一个不含糊的比例：99% 的书只提供娱乐或资讯，扫描即可；值得完整做
一次分析阅读的书极少；值得反复重读的不到一百本。**这个比例判断不是检索问题。** 相关性排序只回答
"这本书和这个词有多近"，不回答"值不值得我花掉这两天"。

审美的来源是**代价**。一本书值不值得，取决于你为它放弃了什么。AI 读得快，但它不为放弃付代价，
所以它天生排序的是信息量与相关性，而不是值不值得。它能做的，是把候选、评分、封面、格式整整齐齐
摆到你面前（`book-fetch` 干的正是这件事），然后**停在那里，等一个它给不出的决定**。

审美也是靠**读坏的**练出来的。你读过足够多不值得的书，才知道什么值得——这个过程不能代理，
就像你不能靠别人替你健身来获得肌肉。

具体到这条流水线，"读书的审美"至少落在三个地方：

| 品味 | 体现在哪 | 对应到哪个 skill |
| --- | --- | --- |
| **选书的品味** | 什么值得读 | `book-fetch` 的六维评分只是铺路的证据，决定由人下 |
| **判断的品味** | 这本书说得对不对、我信不信 | `read-a-book` 把「评·判断」单列一层，逼出一个立场而不是复述 |
| **表达的克制** | 一本书只留一句话时，留哪句 | 这决定了 Share 段的选题标准：给每本书找一句能被记住的话（"创作最大的敌人，不是没灵感"），而不是写摘要 |

这也是仓库叫 reading-pkm 而不是"读书自动化"的原因：**能被自动化的部分越多，越要清楚哪一部分不能自动化。**
说得再直白一点——AI 可以替你 sense，也可以替你 share，但它不能替你**在意**。

## 分享：小红书上的样子

Share 段目前只有一条渠道跑通：小红书，账号 **司马读书**（[主页](https://www.xiaohongshu.com/user/profile/61fabcd80000000010008da7)）。
截至 2026-10-07，共 262 条笔记、2405 粉丝、2.2 万获赞与收藏。

![个人主页](docs/profile.png)

其中 15 篇是这条流水线的成品——每本书先做分析阅读，压成卡片，再渲染成 5 张 1080×1440 的图文。
同一批模板跑出来的封面，题材不同、版式不变：

![六个不同选题的封面](docs/covers.png)

一篇笔记的完整五张卡（Rick Rubin《The Creative Act》），从封面到"来处／去处"：

![一篇笔记的五张卡片](docs/case-post.png)

## 架构

```mermaid
flowchart LR
    Q["问题"] --> R["brain.py recall"]
    R --> ARMS["四路各投一票<br/>维度 · 字面 · 小节 · 关系边"]
    ARMS --> RRF["RRF 名次融合<br/>不手调权重"]
    RRF --> C["候选 6 本<br/>命中 · 置信 · 边界 · 可当反方"]
    C --> ANS["只读 1–2 张卡<br/>给出带出处的意见"]
    DIMS["dimensions.md<br/>25 维，人为固定"] -.-> ARMS
    IDX["index.json<br/>随书数涨，不进上下文"] -.-> ARMS
    EV["evals：31 题 + 基线"] -.闸门.-> R
    ANS --> SB["library/books/ 卡片 + library/notes/ 原笔记"]
```

**一条不变量：一次问答进上下文的东西，与书库规模无关。** 书库涨到上千本，
`library/index.md` 仍是 ≈2K tokens（它按维度排，规模只随维度数变），随书数增长的
`index.json` 从不进上下文，真正读进去的永远是 1–2 张卡。省下的这笔上下文，是用**路由准确度**付的，
所以重点不是"检索得全"而是"路由得准"。

**五个面各管一件事。** Skill 管"怎么想"，文件管"想什么"（`library/books/` 是内容层、`library/notes/` 是深度层、
`library/evals/` 是题集），`brain.py` 管"找得到"，`dimensions.md` 是人唯一的调参旋钮，`rsi.py` 只读地量闭环。
真相只有 markdown——`index.md`／`index.json` 都是生成物，删了能重建。

**机器只做确定性的事。** `brain.py` 纯标准库、零 LLM：关系边是受控词表 + `《书名》`的正则解析，
排序是四路名次融合，小节命中按覆盖度打分。凡是能变成工具输出的纪律（置信、边界、可当反方）
就不留在散文里等模型记性。

**明确不引入的东西。** 没有向量库与嵌入、没有常驻服务、没有数据库：判断型问题靠维度表取视野，
而不是靠相似度；一次问答 4–9K tokens，而不是把书库读一遍（卡片全读一遍就是几万 tokens 起，
而且它随书数线性涨）。

**改坏了怎么知道。** `brain.py check` 查结构，`brain.py eval` 查效果（31 题固定集出 P@k／MRR，
回退超 0.05 退出码 1），`eval` 的「未调参」那一行才是泛化口径。`rsi.py` 量的是另一件事——
闭环覆盖率与断点，没埋点的指标就明写"未埋点"。

深挖见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)：七个关键决策、与
[gbrain](https://github.com/garrytan/gbrain) 的取舍对照、扩展点与已知缺口。

## 仓库结构

**代码和数据分开放**：skill 在 `skills/`，书库在 `library/`。这样每个 skill 能单独拷走用，
书库也能整体搬到仓库外（换机器、挂网盘），而不用改任何代码。

```
skills/               代码面：九个 skill，各自独立，单个拷出去也能跑
  read-a-book/        阅读方法论：检视 / 分析 / 主题阅读
  book-fetch/         SEEK：取得书
  second-brain/       第二大脑：写法、路由表（25 维）、工具（brain.py）
  scandinavian-style/ 版式系统：16:9 网格、one-pager、图表规则
  rednote-post/       SHARE：渲染成卡片并发布
  rednote-assistant/  SHARE：排期与节奏
  rednote-reply/      SHARE：私信只读巡检 + 按人设拟稿
  human-writing/      写作层：去 AI 味 + 贴人设（被 rednote-post 调用）
  rsi-reading/        站在上面那层：度量整条环，一次只改一处
library/              数据面：卡片与笔记、评测集、索引——真相在这里
  books/              每本一张卡（只读选中的）
  notes/              原笔记（深度层）
  evals/              31 题固定题集 + 基线
  index.md index.json 生成物，删了能重建
docs/                 README 用图与其生成脚本；ARCHITECTURE.md 架构说明
.codex-plugin/        plugin.json：把九个 skill 一次装进 Codex
```

`brain.py` 找书库的顺序是 `$SECOND_BRAIN_HOME` → 仓库里的 `library/` → `~/.local/share/second-brain`，
所以从任何目录跑结果都一样；skill 单独拷出去也能用，只要让 `$SECOND_BRAIN_HOME` 指着书库（见下节）。

## 安装

**这个仓库就是一个 Codex plugin。** 清单 `.codex-plugin/plugin.json` 里只声明了一件事——
`"skills": "./skills/"`（plugin 还能声明 `apps`／`mcpServers`／`hooks`，本仓一个都不需要）。
把仓库当本地 plugin 装进 Codex，九个 skill 一起就位，名字带 plugin 前缀
（`reading-pkm:second-brain`、`reading-pkm:book-fetch`…）。

不想用 plugin 就手工复制（装出来的是不带前缀的同名 skill）：

```bash
for s in book-fetch read-a-book second-brain scandinavian-style \
         rednote-post rednote-assistant rednote-reply human-writing rsi-reading; do
  cp -R "skills/$s" "${CODEX_HOME:-$HOME/.codex}/skills/"
done
```

**装完记得让 `brain.py` 找得到书库。** 它按 `$SECOND_BRAIN_HOME` → 兄弟目录 `library/` →
`~/.local/share/second-brain` 找。直接从仓库跑、或上面那种软链装法，`library/` 就在 skill 旁边，
什么都不用设；skill 被拷到别处（plugin 装法会把插件放进 Codex 自己的目录）时它就不在旁边了——
这时指一下，一个环境变量的事：

```bash
export SECOND_BRAIN_HOME="$HOME/Documents/GitHub/reading-pkm/library"
```

书库放错地方时命令不会闷着——`brain.py index`／`check`／`recall` 会把解析出来的路径印出来。

开发时直接软链，改仓库即生效（软链的路径指向 `skills/` 里的目录）——本机眼下就是这一种：
应用侧看到的名字是 `reading-pkm:*`，指过去仍是仓库里的这份文件。

```bash
for s in book-fetch read-a-book second-brain scandinavian-style \
         rednote-post rednote-assistant rednote-reply human-writing rsi-reading; do
  ln -sfn "$PWD/skills/$s" "${CODEX_HOME:-$HOME/.codex}/skills/$s"
done
```

两个 skill 需要额外配置，配置都在仓库外——**仓库里不含任何凭据或书源信息**：

- `book-fetch`：书源适配器，见 [`skills/book-fetch/SKILL.md`](./skills/book-fetch/SKILL.md)
- `rednote-post`：浏览器桥接与扩展，见 [`skills/rednote-post/references/publishing.md`](./skills/rednote-post/references/publishing.md)

---

配图都是可复现的：框架图源文件在 [`docs/src/framework.html`](./docs/src/framework.html)，
主页与卡片拼图由 `python3 docs/src/build_images.py` 生成。
