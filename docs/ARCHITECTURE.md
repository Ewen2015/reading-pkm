# 架构

这份文档回答**为什么这么切**。怎么用看各 skill 的 `SKILL.md`；流水线现在跑到哪一步、
哪条回流是断的，看 [`skills/rsi-reading/references/loop-map.md`](../skills/rsi-reading/references/loop-map.md)，
本文不重复那张图。

参考对象：[garrytan/gbrain](https://github.com/garrytan/gbrain)——同样是「把 markdown 变成
可检索的大脑」。它选了 Postgres／pgvector + 常驻 MCP 服务；本仓选了**文件即大脑 + 按需加载**。
两边的取舍差别集中列在第 6 节。

文中的 token 数都是 `brain.py est_tokens`（≈1.6 字/token）量出来的量级，不是精确值。

## 0. 仓库长什么样

```
reading-pkm/
|-- skills/                 代码面：九个 skill，各自独立，单个拷贝出去也能跑
|   |-- second-brain/       第二大脑（唯一"有内部架构"的 skill，见第 3 节）
|   |-- read-a-book/        阅读方法论：检视 / 分析 / 主题
|   |-- book-fetch/         SEEK：取得书
|   |-- scandinavian-style/ 版式系统
|   |-- rednote-post/       SHARE：渲染与发布
|   |-- rednote-assistant/  SHARE：排期与节奏
|   |-- rednote-reply/      SHARE：私信回复（默认只读）
|   |-- human-writing/      写作层：去 AI 味 + 贴人设
|   `-- rsi-reading/        闭环度量
|-- library/                数据面：卡片、原笔记、题集、索引——真相在这里
|   |-- books/  每本一张卡       notes/  原笔记
|   |-- evals/  固定题集与基线    index.md / index.json  生成物
|-- docs/                   这份说明 + README 的配图与生成脚本
|-- .codex-plugin/          plugin.json：把九个 skill 一次装进 Codex
`-- README.md
```

**为什么代码和数据分家。** 三条理由，一条比一条实际：

1. **skill 要能单独装。** `skills/<name>/` 拷进 `~/.codex/skills/` 就能跑，不带书库也行——
   `brain.py` 找不到库就在 `~/.local/share/second-brain` 建一个空的。
2. **数据要能整体搬。** 书库换机器、换位置（本机／网盘／共享盘）只改一个环境变量，
   不动任何 skill 代码。
3. **`git` 要能分得清。** "改工具"和"加一本书"的 diff 不再混在一起；将来想只公开 skill、
   不公开书库，排除一个目录就够了。

**路径解析：只认三条，不认"仓库在哪"。** `brain.py` 与 `rsi.py` 用同一套顺序，取第一个命中的：

| 顺序 | 位置 | 什么时候用得上 |
| --- | --- | --- |
| 1 | `$SECOND_BRAIN_HOME` | 显式指定；书库放在仓库外时 |
| 2 | skill 目录的**兄弟目录** `library/` | 本仓库的默认形态，从任何 cwd 都对 |
| 3 | `~/.local/share/second-brain` | 兜底；没有库就在那里初始化一个空的 |

所以仓库可以整个改名、挪位置，代码不用动；也可以把某个 skill 单独拷出去、数据放别处。
**"从哪个 cwd 跑"与"仓库在哪"都不是运行时前提**——这是把路径解析写进代码而不是写进
文档的原因。**代价要认**：按 plugin 装（skill 落在 plugin 目录下）时 `library/` 就不在
skill 旁边了，得显式 `export SECOND_BRAIN_HOME=<书库>`；空书库时命令会把这个路径印出来，
不让人对着"没有卡片"干猜。

**跨 skill 找文件：按 skills 根找，不按仓库相对路径找。** 例：`rednote-post` 的
`check_content.py` 要用 `human-writing` 的 `human_check.py`。装完之后这两个 skill 是
`~/.codex/skills/` 下的兄弟目录，在仓库里是 `skills/` 下的兄弟目录——同一条顺序两种情况都成立：

1. 自己的兄弟目录（仓库里 = `skills/`，装完 = `~/.codex/skills/`）
2. `$CODEX_HOME/skills`
3. `~/.codex/skills`
4. `~/.agents/skills`

这段查找逻辑**在每个用得着的 skill 里各留一份**（几十行），不抽成共享模块：共享模块会让
"单独拷一个 skill 就能跑"失效。这是**故意的**重复，代价是改规则要改几处。

由此定下文档里的写法约定：**代码里的路径串是"查找键"，按 skills 根写**（如 `human-writing/scripts/human_check.py`，
改一个字查找就会失败，所以和代码保持一致）；**散文与文档里的路径是"给人看的"，按仓库根写**
（如 `skills/human-writing/scripts/human_check.py`）。两套写法指向同一个文件，但谁都不是废话。

**打包形态：一个 plugin，九个 skill。** 仓库根放一份 plugin 清单
（`.codex-plugin/plugin.json`），只声明 `"skills": "./skills/"`——Codex 按目录把九个 skill
一起装上（名字带 plugin 前缀，`reading-pkm:second-brain`），不用手写 `cp` 循环。
清单里没有 `apps`、没有 `mcpServers`、没有 `hooks`，`interface` 也只有一个标题：
和"无服务、文件即大脑"是同一条纪律——**这个 plugin 里除了 skill，什么都没有**。

**和 bundle 的关系：bundle 说的是来源，不是格式。** Codex 里随应用一起分发的插件，来自一个
**名字**叫 `openai-bundled` 的市场（`config.toml` 里就是两行：`[marketplaces.openai-bundled]`
加 `[plugins."visualize@openai-bundled"]`）；另一批系统能力插件来自
`openai-primary-runtime`，官方目录里的三方插件来自 `openai-curated`——三者用的是**同一份**
`.codex-plugin/plugin.json`，差别只在谁提供、装到哪。所以本仓不是 bundle，是一个本地/第三方
plugin；`openai-docs` 给 plugin 的定义也正是"an installable bundle of skills, tools, …"，
即 bundle 是**装什么**的泛指，plugin 才是**打包单位**。

**要不要再拆成 marketplace？** 一个市场用 `<root>/.agents/plugins/marketplace.json` 列插件，
`source` 可以是 `local`（本地目录）、`url`（一个 git 仓库）或 `git-subdir`（仓库里的某个子目录）。
也就是说：想让人**只装其中一个 skill**，或者想从 GitHub 直接分发，就得再加这份清单，把
`skills/` 拆成多个 plugin 目录。本仓现在**有意不拆**——这条流水线本来就是一套的东西
（缺了 `second-brain`，`read-a-book` 的产物没处沉淀），整体装、整体用，清单也就少一份要维护的。
哪天要单独发某一个 skill，再加不迟。

## 1. 一条不变量

**一次问答进上下文的东西，与书库规模无关。**

这条决定了后面所有的取舍。书库涨到上千本时：

| 进上下文的东西 | 现在 | 1000 本时 |
| --- | --- | --- |
| `library/index.md`（唯一常读的文件） | ≈1.9K tokens | ≈2K——只随**维度数**变 |
| `library/index.json`（机器层，**从不进上下文**） | ≈0.2 MB | 线性增长，无所谓 |
| `books/<slug>.md` 单张卡 | 1.4–3K tokens | 不变 |
| 一次问答真正读进去 | 1–2 张卡 | 1–2 张卡 |

代价很直接：**省下的这笔上下文，是用路由层的准确度付的**。检索必须在「指路」这一步就做对，
读错一张卡的成本无法靠"多读几张"摊薄。所以本仓的重点从"检索得全"变成"路由得准"，
也所以有了维度表和 eval 闸门（第 5 节）。

## 2. 五个面

```mermaid
flowchart TB
    subgraph Skills["Skill 面：skills/ 下九个目录，各自独立，靠 SKILL.md 描述何时被调用"]
        BF["book-fetch"] --> RAB["read-a-book"] --> SB["second-brain"]
        RAB --> STY["scandinavian-style"] --> RP["rednote-post"]
        RP --> HW["human-writing"]
        RA["rednote-assistant"] -.排期.-> RP
        RP -.词表.-> RR["rednote-reply"]
        RSI["rsi-reading"] -.只读观测.-> Skills
    end

    subgraph Data["数据面：library/ 下，真相都在文件里"]
        NOTES["library/notes/ 原笔记（深度层）"]
        CARDS["library/books/ 卡片（内容层）"]
        EV["library/evals/recall.jsonl（固定题集）"]
    end

    subgraph Engine["引擎面：skills/second-brain/scripts/brain.py，纯标准库，零 LLM"]
        RECALL["recall：四路召回 + RRF"]
        INDEXCMD["index：生成 index.md 与 index.json"]
        CHECK["check：体检卡片与关系边"]
        EVAL["eval：P@k／MRR + 基线闸门"]
    end

    subgraph Route["路由面：人为固定"]
        DIMS["skills/second-brain/references/dimensions.md（25 个维度）"]
    end

    RAB --> NOTES --> CARDS
    CARDS --> INDEXCMD --> IDXMD["library/index.md（指路牌，每次挑维度时读）"]
    CARDS --> RECALL
    DIMS --> RECALL
    RECALL --> SB
    CARDS --> CHECK
    EVAL -.闸门.-> RECALL
```

五个面各自的职责边界：

| 面 | 由谁负责 | 什么时候进上下文 | 谁能改它 |
| --- | --- | --- | --- |
| Skill 面 | 九个 `SKILL.md` | 命中 skill 时加载 | 人 |
| 数据面 | `library/books/` `library/notes/` `library/evals/` | 按需：卡片 1–2 张；题集从不 | 人（模型只在被问到时提议） |
| 路由面 | `skills/second-brain/references/dimensions.md` | 改词表时 | 人——**这是唯一的调参旋钮** |
| 引擎面 | `skills/second-brain/scripts/brain.py` | **从不**（只吐命令输出） | 代码评审 |
| 度量面 | `library/evals/` + `skills/rsi-reading/scripts/rsi.py` | 从不 | 人 |

一句话概括分工：**skill 管"怎么想"，文件管"想什么"，脚本管"找得到"，eval 管"有没有变差"。**

## 3. second-brain 的四层，和一次问答的账

`second-brain` 是整个仓库里唯一有"内部架构"的 skill，因为只有它要在书数增长时保持恒定成本。

| 层 | 文件 | 进上下文吗 | 规模随什么变 |
| --- | --- | --- | --- |
| 路由层 | `skills/second-brain/references/dimensions.md` | 改词表时 | **人为固定 25 维** |
| 指路牌 | `library/index.md`（生成物） | 每次挑维度时全读 | 维度数 |
| 机器层 | `library/index.json`（生成物） | **从不** | 随书数线性增长 |
| 内容层 | `library/books/<slug>.md` | 只读选中的 1–2 张 | 单张 1.4–3K tokens |
| 深度层 | `library/notes/<slug>.md` | 只在深读／核对出处时 | 单本 3–12K tokens |

`index.md` 能"不随书数涨"的原因是：它按**维度**排，每维只列 4 本代表书加一句"另有 N 本"。
书变多只是每维的候选变多，行数不变。代表位按卡片 `weight` 排，同 weight 按读完日期近的排——
`recall` 的同分排序用的是同一套规则，两边不会给出矛盾的顺序。

一次判断型问答的预算：

```
SKILL.md ≈3.4K + index.md ≈1.9K + 2 张卡 ≈4K ≈ 9K tokens（不含对话本身）
```

`index.md` 可以不读（拿不准时直接 `recall`），卡也不是必读两张。SKILL.md 里写的
"一次问答 ≈4–6K" 是省着用的算法；上面这个数是不省的上限。**两个数都远小于"把书库读一遍"**——
把全库卡片读一遍就是几万 tokens 起，而且它随书数线性涨。

## 4. 七个关键决策

**D1 · 文件即大脑，索引是缓存。**
真相只有 `library/books/*.md` 和 `library/notes/*.md`；`index.md`／`index.json` 是生成物，删了能重建。
换来的是可 diff、可评审、换机器不用导出导入。代价：全量重建要扫全部卡片（秒级），
不做增量索引；`index.json` 的 `INDEX_VERSION` 一改就整份作废重算，宁可慢也不吃旧格式的暗亏。

**D2 · 维度表是路由层，不是书单。**
25 个思考维度人为固定，问法写**用户会说的话**（`辞职`／`单干`／`用时间换钱`），
不写章节标题。判据是"哪个视野能给这题加一维"，不是"哪本书谈过这个话题"。
代价：加维度要克制（加一个 `index.md` 多三行），且词表要长期维护——换种说法搜不到就补问法。

**D3 · 排序用 RRF 名次融合，不手调权重。**
四路（维度／字面／小节／关系边）各投一票，`score = Σ 1/(60 + rank)`。
手调权重就是在 31 道题上过拟合；要提分只能改词表与阈值，改完过闸门。
代价：解释性差一点，单路的强弱无法体现——用输出的 `命中` 行补回可解释性。

**D4 · 关系边零 LLM 抽取。**
卡片的「与其他书」是散文，脚本只认受控词表 + `《书名》`（正则可解析），认不出类型记「相关」。
不引入模型做实体识别，是因为"抽得准"不是这里的瓶颈，"抽得稳、可复现、可 review"才是。
代价：写法必须规范（`skills/second-brain/references/cards.md` 里规定了词表和格式），`check` 会盯着。

**D5 · 工具输出即护栏。**
SKILL.md 的「必须有出处」「边界是护栏」不是靠模型记，是靠 `recall` 每本候选直接印
`命中 · 置信 · 边界 · 可当反方`。凡是能变成输出的纪律，就不留在散文里。

**D6 · 脚本不碰语义，判断留给人。**
`brain.py` 只回答"该出现的书有没有出现在候选里"，不回答"书里说了什么、该不该这么用"；
`rsi.py` 只读、不写。语义判断在模型侧，品味判断在人侧。这条边界让脚本永远可以整体重写，
而不影响已积累的卡片。

**D7 · 代码和数据分家，路径靠解析不靠约定。**
skill 在 `skills/`，书库在 `library/`；`brain.py` 用「环境变量 → 兄弟目录 → XDG 兜底」
三级解析找它，而不是写死仓库路径。换来两件事：**skill 可以单独拷走**、**数据可以整体搬走**，
且"从哪个 cwd 跑"都不影响结果。代价：数据位置不再是"看一眼目录树就知道"，
所以每处用到它的人都得先问解析器——这也是为什么三级顺序要写在 `SKILL.md` 里明说
（第 0 节），而不是留给读者猜。

配套的一条：**跨 skill 的查找逻辑宁可各抄一份，也不抽共享模块。** 抽模块看着更 DRY，
但会让"拷一个 skill 出去就能跑"这条性质失效——那是这个仓库的安装方式，不能牺牲。

## 5. 闸门：怎么知道改坏了

没有度量的改动都只是"我觉得变好了"。第二大脑现在有两道闸门：`check` 管结构，`eval` 管效果。

```bash
python3 skills/second-brain/scripts/brain.py check          # 结构：字段、维度、索引同步、关系边、孤立卡
python3 skills/second-brain/scripts/brain.py eval           # 效果：P@k／R@k／MRR +「未调参」那一行
python3 skills/second-brain/scripts/brain.py eval --check   # 回归：与 baseline.json 比，跌超 0.05 退出码 1
```

- **题集**：`library/evals/recall.jsonl` 31 题（19 vocab／12 paraphrase），每题标 `gold`／`accept`。
  这是**固定的**——题一次写死，之后只改词表，不改题（改了题就没法比）。
- **口径**：因补问法而变好的题标 `tuned`，单独算「未调参」那一行。现在未调参 21 题
  MRR 0.897、维度命中率 90.5%。**泛化看这一行，不看整体。**
- **能力边界**：这个数只量"候选里有没有该出现的书"，不量"答案好不好"。
  后者要人看——把 11 条铁律变成可判分的题是下一个待办。
- **闭环度量**：`skills/rsi-reading/scripts/rsi.py` 量的是另一件事——这条流水线的闭环覆盖率、
  断点数、端到端周期。口径在 [`skills/rsi-reading/references/metrics.md`](../skills/rsi-reading/references/metrics.md)，
  没埋点的指标就明写"未埋点"，不假装有数。

## 6. 与 gbrain 的取舍对照

| | gbrain | reading-pkm |
| --- | --- | --- |
| 真相存哪 | Markdown 为源，索引进 Postgres／PGlite | Markdown 就是全部，`index.json` 是可重建的缓存 |
| 检索 | 混合检索（向量 + BM25 + 图）+ 交叉编码器复排 | 四路 RRF（维度／字面／小节／关系边），零嵌入 |
| 关系 | 实体图：人／公司／项目／投资 | 书与书的关系边：对撞／上游／下游／同体例 |
| 部署形态 | 常驻 MCP 服务 + 定时任务群 | 无服务：命令 + 文件，随用随跑 |
| 知识单位 | 页面／实体／事实／主张 | 一本书的**视野**，挂在 25 个思考维度上 |
| 质量闸门 | 大规模 eval + 校准曲线 | 31 题固定集 + 基线闸门 + 口径声明 |
| 共享 | 多 brain／团队挂载／细粒度权限 | 单机单库；`library/notes/` 与 `library/books/` 一起入库 |

同样的判断纪律（抽取不用 LLM、来源冲突两边都留、指标要自我披露口径）两边都取；
基础设施（向量库、图库、常驻服务、人物实体模型）不取——那与本仓的"文件即大脑、
成本不随书数涨"直接冲突。

## 7. 扩展点

| 想加什么 | 怎么做 | 别忘了 |
| --- | --- | --- |
| 一本书 | `brain.py add --notes <笔记> --slug <s>` → 填卡 → `index` | `check` + `eval --check` |
| 一个维度 | 改 `skills/second-brain/references/dimensions.md`；只有现有维度真的装不下才加 | 加一个维度 `index.md` 多三行，且要补 3 条以上问法 |
| 一道评测题 | 往 `evals/recall.jsonl` 加一行 JSON | 别标 `tuned`——不标才算泛化样本 |
| 一种呈现／渠道 | 走 `scandinavian-style`（版式）与 `rednote-post`（渲染发布） | S4 是**分支**不是节点：载体／渠道／呈现三层要分开 |
| 一个新的思考面 | 先看 `skills/rsi-reading/references/backlog.md` 有没有记过 | 一次只改一处，改完记账 |

## 8. 已知缺口

- **回流断着**：各渠道的反馈没有回到选题与呈现（回流 A）；`second-brain` 的取用记录
  没有回到书库取舍（回流 B）。两条都标在 loop-map 里，`rsi.py` 只在埋了点的地方报数。
- **有一类问题没有维度**：像"想不出来、认命了"这种"问题本身可不可解"的题，
  25 维里没有落点，只能靠字面路命中（如 q28）。
- **关系边写得不齐**：16 组「对撞」只写了单边；`analysis-and-thinking`、`historical-regimes`
  是孤立卡（没有库内关系边）且缺「常被引用的原句」。这些是卡片改进 backlog，不影响可用。
- **只量候选不量答案**：见第 5 节。
- **`.recall-log.jsonl` 要攒够 15 行**才报"死卡"，新库上这条体检是静默的。

## 相关文档

- [`../.codex-plugin/plugin.json`](../.codex-plugin/plugin.json)——plugin 清单（skill 装在哪）
- [`../skills/second-brain/SKILL.md`](../skills/second-brain/SKILL.md)——第二大脑的四步与铁律
- [`../skills/second-brain/references/retrieval.md`](../skills/second-brain/references/retrieval.md)——四路召回怎么读、词表怎么改
- [`../skills/second-brain/references/cards.md`](../skills/second-brain/references/cards.md)——卡片字段与关系边写法
- [`../skills/rsi-reading/references/loop-map.md`](../skills/rsi-reading/references/loop-map.md)——流水线实测状态
