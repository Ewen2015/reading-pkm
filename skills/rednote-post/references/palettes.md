# 书主题色板

每篇新 post 只选一套色板。先判断**这本书在讲什么、读者带着什么问题进来**，再看书封颜色；
不能只按“最近喜欢哪种风格”选。视觉样片见 [`palette-directions.jpg`](palette-directions.jpg)。

![三套书主题色板样片](palette-directions.jpg)

## 选择顺序

1. 用一句话写主题：这本书主要处理**知识解释、能力成长、还是情绪与表达**？
2. 按下面的表选一套；一套色板贯穿封面和全部内容卡，不混搭。
3. 在 `manifest.json.palette.reason` 写清判断，不只写色板名。
4. 跑 `apply_palette.py` 落地 CSS：

```bash
python3 <skill>/scripts/apply_palette.py --list
python3 <skill>/scripts/apply_palette.py <post-dir> emerald-signal \
  --reason "成长与长期训练主题：绿色承载生长，陶红标阶段，黄色标时间尺度"
```

脚本会复制 `build/palette.css`、按顺序接到 `card.css` 后面，并写入：

```json
"palette": {
  "id": "emerald-signal",
  "name": "祖母绿信号",
  "reason": "成长与长期训练主题：绿色承载生长，陶红标阶段，黄色标时间尺度"
}
```

## 三套色板

| id | 名称 | 主题信号 | 适合 | 避开 |
| --- | --- | --- | --- | --- |
| `emerald-signal` | 祖母绿信号 | 生长、落地、长期 | 成长、领导力、组织、商业、自然、韧性 | 需要冷静技术图纸感的内容 |
| `nordic-functional` | 北欧功能主义 | 冷静、分析、秩序 | 科学、技术、经济、制度、战略、哲学、认知 | 强情绪、派对感、轻内容 |
| `dopamine-pop` | 多巴胺波普 | 能量、反差、传播 | 创造力、行为心理、文化、潮流、青年、反常识 | 战争、灾难、死亡、创伤、危机复盘 |

### 默认规则

- 拿不准时用 `emerald-signal`。它是当前默认，不再沿用旧的暖纸 + 安全橙编辑风。
- 严肃公共议题优先 `nordic-functional`，不是默认套绿色。
- `dopamine-pop` 可以只用于封面或单篇短内容；如果五张都承载长解释，改用前两套。
- 书封本身颜色很强时，先保证书封是识别主体，再用色板收住页面；不要为了配色把书封叠滤镜。
- 同一账号连续多篇可以轮换色板，但单篇内不要一会儿北欧、一会儿多巴胺。

## 色值

### 祖母绿信号

`#317039` 绿 / `#F1BE49` 黄 / `#CC4B24` 陶红 / `#F8EDD9` 奶油 /
`#FFF1D4` 木瓜 / `#FFF8EB` 拿铁。

### 北欧功能主义

`#E6ECE8` 冷灰绿 / `#17211D` 墨黑 / `#D64B33` 信号红 / `#2C665E` 峡湾绿 /
`#F8F9F6` 白面。

### 多巴胺波普

`#FF5A9D` 热粉 / `#FFE45C` 柠檬黄 / `#3158FF` 钴蓝 / `#111111` 黑 /
`#FFF8EB` 奶油底。

## 判断例子

- 《如何在自己感兴趣的领域出类拔萃》：成长与学徒期 → `emerald-signal`。
- 《科学革命的结构》：范式、证据与知识制度 → `nordic-functional`。
- 一本讲创作习惯、行为改变或文化观察的书 → `dopamine-pop`。
- 金融危机亲历、战争、灾难、疾病回忆录 → `nordic-functional` 或 `emerald-signal`，不用多巴胺。

## 使用约束

- `--accent` 只做短句、关键词、序号和引用边线；整段正文走 `--ink2`。
- `--accent-wash` 必须和 `--accent` 同色系，不能一个色板里出现两套信号色。
- 卡片 HTML 里的局部硬编码颜色要改成 `var(--accent)` / `var(--accent2)` / `var(--accent3)`。
- 选完后把理由写进 manifest；`check_content.py` 会核对 `palette.id`、`reason` 和实际 `--accent`。
