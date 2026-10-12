# 发布环境

## 它怎么工作

不去驱动一个全新的无头浏览器——**借用用户自己已登录的 Chrome**：

```
scripts/xhs.py  ──WebSocket:9333──▶  bridge_server  ──▶  Chrome 扩展  ──▶  当前标签页
```

扩展是 `autoclaw-cc/xiaohongshu-skills` 提供的（仓库里叫 `XHS Bridge`）。好处是登录态、
验证码、风控指纹都是真人浏览器，不需要处理扫码和 cookie 导出。

**代价是：扩展作用在"当前活动标签页"上。** 用户切走标签页，命令就会打到别的页面上。
跑之前先确认 Chrome 开着且停在发布页。

## 一次性配置

```bash
bash <skill>/scripts/setup_bridge.sh
```

它会克隆仓库、建 venv、装依赖，并按下面的坑固定 `websockets` 版本。装完还需要手动做两件事：

1. `chrome://extensions` → 打开开发者模式 → 加载已解压的扩展 → 选仓库里的扩展目录；
2. 在 Chrome 里登录小红书创作服务平台。

### 唯一的硬坑：websockets 版本

**必须锁 `websockets==13.1`。** 更高的版本（16.x）握手协议不兼容——服务端日志显示
"Extension 已连接"，但 CLI 侧必定拿到 `InvalidMessage`。这个错看起来像扩展没装好，
其实是版本问题，会浪费很久。

`setup_bridge.sh` 里已经固定，别随手升级。

## 目录与状态

默认路径 `/tmp/xhs-skills-repo`，可用环境变量覆盖：

```bash
export XHS_SKILLS_DIR=~/code/xiaohongshu-skills
```

**`/tmp` 会被系统清理。** 依赖长期使用就把仓库放到持久目录。

## 桥接的两种用法

**CLI**（表单填写、发布这类标准动作）：

```bash
.venv/bin/python scripts/cli.py fill-publish --title-file T --content-file B --images 1.jpg 2.jpg ...
.venv/bin/python scripts/cli.py click-publish
```

**`BridgePage`**（自定义步骤：回读页面、点合集下拉等），`<skill>/scripts/xhs.py` 用的就是这条：

```python
import sys; sys.path.insert(0, f"{REPO}/scripts")
from xhs.bridge import BridgePage
p = BridgePage("ws://localhost:9333")
p.navigate(url); p.evaluate("..."); p.click_element(sel)
```

注意 CLI 每条命令会**自己拉起又关掉**一个 bridge。要用 `BridgePage` 就得另起一个常驻的：

```bash
nohup .venv/bin/python scripts/bridge_server.py > /tmp/xhs-bridge.log 2>&1 &
```

### 长正文会撞上命令超时（要打补丁）

上游把命令超时硬编码在 90s（`xhs/bridge.py` 的 `ws.recv` 和 `bridge_server.py` 的
`asyncio.wait_for`）。正文是逐字输入的，**~900 字带 8 个话题标签时会跑过 90s**，
症状是 CLI 报「命令执行超时（90s）」而页面上其实已经填好了——重跑会把内容填两遍。

补丁在 [`../patches/`](../patches/)，改成读 `XHS_CMD_TIMEOUT`（默认 900s）：

```bash
cd "$XHS_SKILLS_DIR" && git am <path>/bridge-command-timeout.patch
```

已提上游 PR [autoclaw-cc/xiaohongshu-skills#102](https://github.com/autoclaw-cc/xiaohongshu-skills/pull/102)；
合入之后这段就可以删掉。

**默认值为什么是 900s 而不是"正常要多久"**：`CMD_TIMEOUT` 在**进程启动时**读一次。
一个用旧默认起着的常驻 server 会一直占着 9333，之后每条 CLI 都连到它、拿到那个旧上限——
**这时在 shell 里 `XHS_CMD_TIMEOUT=900 python3 xhs.py fill` 是完全没用的**，
因为环境变量属于调用方，不属于那个已经在跑的进程。默认值贴着"正常要多久"，
就会在"有人先起了一个 server"这种最普通的情况下悄悄失效。

所以 `xhs.py` 这一侧做了两件事（`ensure_server`，本项目自己的代码，不在上游补丁里）：

- **发现就换掉。** `ping_server` 会回报 server 自己实际生效的 `cmd_timeout`；
  `ensure_server` 拿它和需要的值比，不够就杀掉端口上那个 `bridge_server.py` 再起一个。
  `xhs.py status` 会把这行打出来，低于 900s 时直接标出来。
- **换完等扩展。** 换 server 会断开扩展（扩展侧 3s 一次重连），不等它就会在紧接着的
  第一条命令上撞「Extension 未连接」——这是换 server 动作**自带的新故障**，
  不是原来那个。`ensure_server` 换完会等最多 25s 确认扩展回来。

回归测法（两条都要过）：

```bash
# 1) 故意起一个旧上限的 server
cd "$XHS_SKILLS_DIR" && XHS_CMD_TIMEOUT=120 nohup .venv/bin/python scripts/bridge_server.py >> /tmp/xhs-bridge.log 2>&1 &
sleep 4
python3 <skill>/scripts/xhs.py status          # 应打印「命令上限 : 120s  ← 低于 900s…」
python3 <skill>/scripts/xhs.py reset           # 应打印「停掉旧 bridge server…」并正常完成
python3 <skill>/scripts/xhs.py status          # 应打印「命令上限 : 900s」
```

**注意 `_kill_port_owner` 只杀命令行里带 `bridge_server.py` 的进程**：
`lsof -ti tcp:9333` 也会列出只是连着这个端口的客户端，按 pid 直接杀会连自己一起带走。

**macOS 上没有 `setsid` / `timeout`**（那是 GNU coreutils）。想让 bridge 活过当前 shell，
`nohup … &` 就够了；但 **Codex 的 `exec_command` 会话结束时会把整个进程组收掉**，
所以每轮发布都在**同一条命令里**先把 bridge 起起来再跑后续步骤，不要跨命令依赖它活着。

## 页面事实（会随小红书改版失效，用前先验）

| 东西 | 选择器 / 事实 |
| --- | --- |
| 发布页 | `creator.xiaohongshu.com/publish/publish?source=official&target=image` |
| 标题框 | `div.d-input input` |
| **正文编辑器** | **`[role='textbox']`（tiptap/ProseMirror）** |
| 图片预览 | `.img-preview-area .pr` |
| 发布按钮 | `.publish-page-publish-btn button.bg-red` |
| 合集按钮 | `.collection-plugin-button` → 选项 `.item-label` |
| 定时发布开关 | 标签文字「定时发布」→ `.custom-switch-wrapper` → `.d-switch-simulator` |
| 定时时间输入 | `input.d-text` 且祖先含 `[class*=datepicker]` |
| 原创声明开关 | 标签文字「原创声明」→ `.custom-switch-wrapper`（在「作品声明」区，不在「更多设置」） |
| 笔记管理 | `creator.xiaohongshu.com/new/note-manager` |

### 正文编辑器已经换了

老版本是 Quill，选择器 `div.ql-editor`。**现在这个元素不存在了**，编辑器是
tiptap/ProseMirror，要认 `[role='textbox']`（或找 `data-placeholder` 含"输入正文描述"
的 `p` 的 textbox 祖先）。

用旧选择器时 `has_element` 返回 false，而正文**其实填进去了**——会产生
"正文长度 0"的假阴性。回读必须用：

```js
document.querySelector('[role=textbox]').innerText.length
```

### 上限

标题 ≤ 20 字 · 正文 ≤ 1000 字 · 图片 ≤ 18 张。CLI 会自己检查标题和正文长度并报错。

### 表单是富交互组件

这些是自定义组件，不是原生表单元素。用 `.click()` 常常没反应；
要按顺序派发完整事件序列：

```js
['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t =>
  el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, clientX:x, clientY:y})));
```

反过来，原生 `<input>` 用 `input` + `change` 事件就够了。

### 话题标签必须用「真点击」——这是最容易踩的坑

正文末尾那行 `#意会认知 #波兰尼 #读书` 不会自动变成话题。小红书要求每个标签都从
**联想下拉**里选一次，而下拉项的选中逻辑**只认真实鼠标事件**（`event.isTrusted === true`）：

| 点击方式 | 结果 |
| --- | --- |
| `el.click()` | ✗ 标签留在编辑器里当成普通文字 |
| 合成 `pointerdown/mousedown/…/click` 序列 | ✗ 同上 |
| **CDP `Input.dispatchMouseEvent`（真点击）** | ✓ 变成 `.tiptap-topic` 话题 |

**匹配要用严格前缀，点击要按坐标。** 联想项文本形如 `#读书笔记17亿浏览`，用
`textContent.includes('创作')` 会把「创作」选成「创作者体验挽回」——必须要求名字在下一个
汉字/字母之前就结束。另外下**拉列表会在探测与点击之间重排**，按 index 点会点到别的项，
要按探测到的坐标点。

上游 CLI 的 `_input_single_tag` 用的是第一种，所以它 **log 说"点击标签联想"其实没生效**。
症状很有迷惑性：所有标签被当成一整串文字，**只有最后一个**因为后面紧跟换行才被识别成话题。

还有两个连带事实：

* 下拉列表是 tippy 弹层，列表节点 id 是 `#creator-editor-topic-container`。
  **它关着的时候也留在 DOM 里**，停靠在页面左上角（压着左侧边栏）、里面是默认热榜
  （第一条永远是 `#读书128亿浏览`）——所以「找得到 `.item`」不等于「下拉是打开的」。
  判据要用 `document.elementFromPoint(中心点)` 确认那个坐标下真的就是下拉项。
* 桥接里只有 `click_element_by_text` / `click_nth_element` 走 CDP 真点击；
  `click_element` 是合成 JS 点击。**标签一律用前者。**

`scripts/xhs.py` 的处理方式：把正文末尾的标签行从交给 CLI 的正文里摘掉（CLI 看不见
`#` 就不会去点它那个坏路径），再自己用真点击逐个提交，最后数
`[role=textbox] .tiptap-topic` 的个数对不对。提交不上时补一个空格，避免它跟下一个标签粘成一片。

单独重跑（不用重新填表）：

```bash
<skill>/scripts/xhs.py tags --dir <post-dir>          # 读 body 末尾的标签行
<skill>/scripts/xhs.py tags --dir <post-dir> --tags 意会认知 波兰尼
```

### 标签就是上不去的时候

观察到的坏状态长这样：编辑器里 `#标签` 的联想是打开的、列表里也有正确的候选，
CDP 真点击也确实发出去了，但**点了不生效**——话题数不变，`#标签` 留成普通文字。
另一种是列表**卡在上一次查询的结果**上（比如一直显示 `#意会认知0浏览`），
等多久都不刷新。这两种都不是选择器问题，重试也救不回来。

`xhs.py` 的处理：每个标签最多重试 2 轮（失败时用浏览器的删除命令原地擦掉再重打，
全程不碰 focus／选区，避免把联想组件带歪）；**连续 2 个标签失败就停手**，
不再空转、也不继续打接口。剩下的标签留在编辑器里可见。

**这时不要发布。** 先 `xhs.py reset` 重新导航（必要时重启 Chrome 或换个时间再试），
再走一遍 `fill`。哪怕真上不去，`verify` 也会用话题数把问题挡在发布前。

### 平台没有这个词时，不点「新建话题」会**静默顶掉一个已有话题**

`#诺贝尔文学奖` 这种"平台确实没有"的话题词，下拉里只有一项 `#诺贝尔文学奖新建话题`。
`xhs.py` **默认不点它**（建一个空话题页多半不是作者的意思），只有显式给
`XHS_NEW_TOPIC=1` 才点。

不点的时候有个很难发现的坏结果：**真点击落在了下拉里已有的那一条上**，
于是页面上多出一个**跟前面某个话题一模一样**的话题。
2026-10-12 那次就是 `#读书笔记` 出现了两次、`#诺贝尔文学奖` 不见了。
真正的坏处不是丢一个标签，是**`verify` 照样报"回读一致"**——它比的是话题**个数**
（页面 8 个 vs 本地 8 个），个数对了就过了。

所以：**核对话题不是数个数，是把话题文本列出来跟本地那行比。**

```bash
# 页面上的话题 chip 列表（跟 body.txt 末行逐项对）
python3 - <<'EOF'
import json, importlib.util, sys
sys.path.insert(0, "/tmp/xhs-skills-repo")
spec = importlib.util.spec_from_file_location("xhs_skill",
    "<skill>/scripts/xhs.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
page, _ = m.bridge()
print(json.dumps(m.chip_texts(page), ensure_ascii=False))
EOF
```

修法：`reset` → `fill`（加 `XHS_NEW_TOPIC=1` 让缺的话题词走新建）→ 再列一遍话题文本。
只重跑 `tags` 不够——重跑时旧的那个重复 chip 还在。

### 「更多设置」的三个坑（2026-10 实测）

**1. 开关要用真点击，标签是唯一稳定锚点。** 「定时发布」「原创声明」都是
`.custom-switch-wrapper` > `.d-switch-simulator`，对它的 `el.click()` 不生效，得用
CDP 真点击打中心点。外层 class 名是哈希（实测见过 `custom-date-picker-44`），
只有标签文字稳定。

**2. 不要用 `.post-time-wrapper input` 找时间输入框。** 那里面**第一个** input 是开关自己的
`<input type="checkbox">`。点到它等于把定时发布又关掉，症状是"时间设上了但开关是关的"。
要用 `input.d-text` 且祖先含 `[class*=datepicker]` 的那个。

**3. 原创声明的两处时序。**
一是「原创声明须知」弹窗里，勾选 `input[type=checkbox]` 后按钮要等一拍才解禁，
同一帧里读会读到 `disabled` 而误判失败；二是弹窗关闭后，全屏的 `.d-modal-mask` 还会在
DOM 里停留一会儿，期间**所有鼠标点击都被它吃掉**——这时去点定时发布开关会毫无反应。
两个都要显式等：等按钮解禁、等 mask 消失。

**但"消失得慢"和"卡死"是两回事。** 实测两次（2026-10-05、2026-10-06）弹窗的 Vue
leave 过渡会**卡在中间态**：`.d-modal-mask` 永远停在 `display:block / opacity:0`，
class 上同时挂着 `portal-fade-enter-from` 和 `portal-fade-leave-active`，
等多久都不会走。`wait_no_mask` 现在会在超时后**自己清掉这个透明遮罩**并继续，
不再是 `die`——因为判据很清楚：**透明度为 0 的遮罩没有任何正当用途**。
真弹窗（`opacity:1`）它不碰。清完 Vue 状态和表单都是完好的，不需要回 `reset` 重填。

## 回读核验（必做）

`fill` 返回的 success 只代表流程跑完，**不代表页面真的对了**。回读这四项并与本地比对：

| 项 | 读法 |
| --- | --- |
| 标题 | `document.querySelector('div.d-input input').value` |
| 正文 | `document.querySelector('[role=textbox]').innerText.length` |
| 图片 | `document.querySelectorAll('.img-preview-area .pr').length` |
| **话题** | `document.querySelectorAll('[role=textbox] .tiptap-topic').length`，**并且把每个 chip 的文本列出来**（见下） |
| 合集 | `.collection-plugin-wrapper` 的 innerText 里是否出现合集名 |

`scripts/xhs.py verify` 做的就是这些。

**话题这一项只数个数是不够的。** 2026-10-12 那次页面上是 8 个话题、本地也是 8 个，
`verify` 报"回读一致"，但文本对不上：`#读书笔记` 出现了两次、`#诺贝尔文学奖` 没了
（缺的那个话题词点击落到了下拉里已有的项上）。所以 `verify` 现在会**再比一次话题文本**
（多重集比较，顺序不管），对不上就报「话题文本对不上」。
自己核对时用 `xhs.py` 的 `chip_names(page)`；机制与修法见
[「平台没有这个词时……」](#平台没有这个词时不点新建话题会静默顶掉一个已有话题)。

## 发布后的模糊结果

`click-publish` 经常打印"15s 内未捕获到任何发布反馈"却仍返回 success。**这是正常的**——
小红书的发布请求不一定走 XHR，toast 也可能一闪而过。

判定以**页面状态**为准：

- URL 出现 `published=true` ✅
- 表单被清空（图片 0、标题空）✅
- 两者同时成立 = 已提交

然后再去 `new/note-manager` 看**已发布**列表里有没有它、**审核中**里有没有它。

## 改稿后换掉一条已经排期的笔记

排期之后才发现文案/卡片要改（R23 就是这种情况），**只能删掉重发**——小红书没有
"编辑已定时笔记"的开放路径，直接再发一条会对同一时刻挂出两条同名笔记。

```bash
# 1. 重渲染，确认布局检查全绿
python3 scripts/render_cards.py <post-dir>
# 2. 重新填表并定时（用同一天的同一个时刻）
scripts/xhs.py reset && scripts/xhs.py fill --dir <post-dir> --collection 司马读书
scripts/xhs.py verify --dir <post-dir> --collection 司马读书
scripts/xhs.py original && scripts/xhs.py schedule "YYYY-MM-DD HH:MM"
scripts/xhs.py publish
# 3. 删掉旧的那条（先不加 --yes 看目标对不对）
scripts/xhs.py delete-note --id <旧 noteId>
scripts/xhs.py delete-note --id <旧 noteId> --title "<标题>" --yes
```

先发新的、再删旧的：中间不会有"两边都没有"的空窗，万一新的一条填表失败，
旧的排期还在。

### `delete-note` 为什么长这样

- **只认 noteId**。标题会重复（新旧的标题一模一样），列表顺序也会变。
  noteId 藏在每张卡 `data-impression` 的 JSON 里：`noteTarget.value.noteId`。
- **不能用 `offsetParent` 判弹窗可见**。删除确认弹窗是 `position: fixed`，
  `offsetParent` 恒为 `null`，用它判会永远跳过弹窗、然后报"弹窗没出现"。
  要用 `getClientRects().length` + 计算样式里的 `visibility/opacity`。
- **弹窗关掉之后仍然留在 DOM 里**（淡出）。所以每次都要重新判"这一次真的弹出来了"，
  否则会点到上一次弹窗的残骸。
- **确认按钮用真鼠标点**。`button.click()` 在这套组件上不稳；
  按 `getBoundingClientRect()` 的中心坐标 `mouse_click` 才吃得准。
- 命令自带二次确认：不加 `--yes` 只打印目标并返回 1；`--title` 可再断言一次标题，
  防手滑填错 noteId。

### 定时发布有窗口上限：**今天 + 13 天**，而且**表单回读会骗人**

2026-10-07 把这条钉死了（《无穷的开始》排 10-21）：

- 设 `2026-10-20 22:00`（今天 + 13 天）→ 卡片上真的存成 `2026-10-20 22:00` ✅
- 设 `2026-10-21 22:00`（今天 + 14 天）→ 卡片上存成 **`2026-10-07 22:45`**，
  也就是「提交时刻 + 1 小时、向上取到 5 分钟」这个默认值 ❌

**危险的地方在于它不报错。** 日期组件把你打的字符串留在 input 里，`schedule` 的回读因此
永远显示你填的时刻、报「已开启、时间正确」；但组件内部收的是"窗口内的时刻"，超窗时它保留
默认值，**提交上去的是默认值**。所以定时发布**不能只看表单回读**——提交后回读卡片：

```bash
python3 scripts/xhs.py verify-scheduled --title "<标题片段>" --expect "2026-10-21 22:00"
```

卡片上写什么，平台就存了什么。对不上就把这条删掉（`delete-note`），成品留存，等窗口放开
再排。发布就绪闸门里对应的检查项是 `plan.py ready` 的「空档在平台定时窗口内」——
空档落在窗口外时**直接判不过**，别排。

> 2026-10-05 记过一次同类现象（排 15 天后，卡片回显提交时刻），当时标的是"未复核"。
> 现在机制、上限和回读方式都清楚了。

### 定时时间在窗口内也可能没提交上——**卡片回读是唯一判据**（2026-10-12 又验一次）

2026-10-07 那次的教训是"超窗时平台静默退回默认值"。2026-10-12 发现同样症状
**不超窗也会出现**：设 `2026-10-15 12:30`（今天 + 3 天，远在窗口内），
`xhs.py schedule` 回读"已开启、时间正确"，提交后卡片上却是
**`2026-10-12 22:35` = 提交时刻 + 1 小时、向上取 5 分钟**——就是那个默认值。
那一次是日期组件这一次没把键入提交进它自己的模型（页面上的 input 值一直是对的）。

处置：**同一档重设一次**（不用改成品），再回读卡片。这次重设就成了。
所以 `verify-scheduled` 不是只给"超窗"用的，**每次提交后都跑**；
对不上先重设一次，还不对才按"超窗/换档"处理。

## 给用户看截图（macOS）

扩展自带的 `screenshot_element` 需要 `<all_urls>` 或 `activeTab` 权限，通常会报
`Either the '<all_urls>' or 'activeTab' permission is required`。别在这上面纠缠，
直接用系统截图：

```bash
open -a "Google Chrome"; sleep 1.5
[ "$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true')" = "Google Chrome" ] \
  && screencapture -x /tmp/shot.png
```

**必须验证 frontmost 再截。** Codex/ChatGPT 窗口会抢焦点，`osascript ... activate`
单独用时经常截到错误的窗口。所以要循环重试直到 frontmost 真的是 Chrome。

## 故障排查

| 症状 | 原因 |
| --- | --- |
| `InvalidMessage` | `websockets` 版本不是 13.1 |
| "浏览器扩展未连接" | 扩展没启用 / Chrome 没开 / 端口被占 |
| 命令打到别的页面 | Chrome 的活动标签页不是发布页 |
| 正文长度读出来是 0 | 用了失效的 `div.ql-editor`；改 `[role=textbox]` |
| 图片数量翻倍 | 没有重新导航到干净发布页就重复 `fill` |
| 点击没反应 | 富交互组件；需要完整鼠标事件序列 |
| 只有最后一个 `#标签` 生效 | 上游 CLI 用合成点击选话题；改用 CDP 真点击（`xhs.py` 已内置） |
| 标签数量对不上 | 数 `[role=textbox] .tiptap-topic`；不是看正文里的 `#` |
