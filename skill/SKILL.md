---
name: game-radar
description: "贴吧内鬼/爆料/强度雷达：抓取鸣潮/绝区零内鬼吧和强度吧、关键词预筛、LLM 判定（爆料真伪/强度分析），并汇报最新情报。"
metadata:
  {
    "openclaw":
      {
        "emoji": "🎮",
        "requires": { "bins": ["python3"] }
      }
  }
---

# 游戏雷达 (Game Radar)

贴吧内鬼/爆料吧和强度吧的抓取、关键词预筛和 LLM 判定工具。数据库在 Windows 侧。

当 Kaelting 问到鸣潮或绝区零的爆料/前瞻/内鬼消息、角色强度/配队/抽卡建议，或要求你拉取最新情报时使用此 skill。

## 能力

1. **拉取最新帖子**（crawl）——抓取贴吧、入库、关键词筛、LLM 判定
2. **查询已有情报**（query）——查数据库里已判定的爆料
3. **查询强度分析**（strength）——强度吧里有价值的强度/配队/抽卡结论
4. **查看抓取日志**（log）——最近几轮的抓取统计

## 命令

### 拉取最新帖子

```bash
cd /mnt/c/Users/streamax/Desktop/龙虾学术 && .venv/Scripts/python.exe -m src.main
```

这会依次执行：抓取 → 快照入库 → 关键词预筛 → LLM 判定。输出示例：
```
[鸣潮爆料] seen=11 new=7 posts+=44 err=None
[鸣潮内鬼] seen=8 new=6 posts+=34 err=None
[鸣潮强度] seen=10 new=8 posts+=50 err=None
[filter] 新标记 signal=7
[llm] 判定完成 judged=3
```

### 查询最新爆料（is_leak=1 且高置信度）

```bash
cd /mnt/c/Users/streamax/Desktop/龙虾学术 && .venv/Scripts/python.exe -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from src.db import connect; from pathlib import Path
conn = connect(Path('data/radar.db'))
for r in conn.execute('''
    SELECT tid, title, forum_kw, llm_summary, llm_confidence, llm_tags
    FROM threads WHERE llm_is_leak=1 ORDER BY llm_confidence DESC, first_seen DESC LIMIT 10
''').fetchall():
    print(f'[{r[chr(34)+\"forum_kw\"+chr(34)]}] conf={r[chr(34)+\"llm_confidence\"+chr(34)]} {r[chr(34)+\"title\"+chr(34)]}')
    print(f'  {r[chr(34)+\"llm_summary\"+chr(34)]}')
    print(f'  tags={r[chr(34)+\"llm_tags\"+chr(34)]}')
    print()
"
```

### 查询强度分析（强度吧里有价值的结论）

强度吧的 `llm_is_leak=1` 表示「有信息量的强度分析」，summary 是核心结论：

```bash
cd /mnt/c/Users/streamax/Desktop/龙虾学术 && .venv/Scripts/python.exe -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from src.db import connect; from pathlib import Path
conn = connect(Path('data/radar.db'))
for r in conn.execute('''
    SELECT t.tid, t.title, t.llm_summary, t.llm_confidence, t.llm_tags
    FROM threads t JOIN forums f ON f.kw = t.forum_kw
    WHERE f.topic='strength' AND t.llm_is_leak=1
    ORDER BY t.llm_confidence DESC, t.first_seen DESC LIMIT 10
''').fetchall():
    print(f'conf={r[chr(34)+\"llm_confidence\"+chr(34)]} {r[chr(34)+\"title\"+chr(34)]}')
    print(f'  {r[chr(34)+\"llm_summary\"+chr(34)]}')
    print(f'  tags={r[chr(34)+\"llm_tags\"+chr(34)]}')
    print()
"
```

### 查询特定关键词的帖子

将 `KEYWORD` 替换为实际搜索词（如「清宵」「卡池」「联动」）：

```bash
cd /mnt/c/Users/streamax/Desktop/龙虾学术 && .venv/Scripts/python.exe -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from src.db import connect; from pathlib import Path
conn = connect(Path('data/radar.db'))
for r in conn.execute(
    'SELECT t.tid, t.title, p.content, p.author_name FROM posts p JOIN threads t ON t.tid=p.tid WHERE p.content LIKE ? AND p.is_signal=1 ORDER BY p.first_seen DESC LIMIT 5',
    ('%KEYWORD%',)).fetchall():
    print(r[0], r[1], '|', r[3])
    print(' ', (r[2] or '')[:150])
    print()
"
```

### 抓取日志

```bash
cd /mnt/c/Users/streamax/Desktop/龙虾学术 && .venv/Scripts/python.exe -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from src.db import connect; from pathlib import Path
conn = connect(Path('data/radar.db'))
for r in conn.execute('SELECT * FROM crawl_log ORDER BY id DESC LIMIT 10').fetchall():
    print(dict(r))
"
```

### 数据库统计

```bash
cd /mnt/c/Users/streamax/Desktop/龙虾学术 && .venv/Scripts/python.exe -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from src.db import connect; from pathlib import Path
conn = connect(Path('data/radar.db'))
for t in ['forums','threads','posts','snapshots','crawl_log']:
    n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t}: {n}')
sig = conn.execute('SELECT COUNT(*) FROM posts WHERE is_signal=1').fetchone()[0]
leak = conn.execute('SELECT COUNT(*) FROM threads WHERE llm_is_leak=1').fetchone()[0]
print(f'signal posts: {sig}')
print(f'confirmed leaks: {leak}')
"
```

## 判定模型（智能路由）

LLM 判定走两阶段，省钱省时间：
- **flash**（`deepseek-v4-flash`）跑全量分类：is_leak / is_bait / confidence / tags
- **pro**（`deepseek-v4-pro`）只对有价值的帖（is_leak=1）生成高质量中文 summary

大部分水贴/非爆料只花一次 flash 调用；threads.llm_model 记录每帖实际用的模型（flash / flash+pro）。
判定只看楼主（主楼 + 楼主补充楼），回复区噪音不喂给 LLM。

## 注意

- 抓取用 Playwright headless Chromium + 贴吧登录态 cookie，cookie 在 Windows 侧 `cookies/tieba.json`
- cookie 过期后需要重新导出（参考 cookies/README.md）
- 高频抓取有封号风险，默认 10 分钟一轮，建议不要缩短间隔
- 数据库路径：`/mnt/c/Users/streamax/Desktop/龙虾学术/data/radar.db`
- 解析器迭代后可从快照重建数据：`python reparse.py`（不用重爬），再 `python repipe.py` 重筛重判

## 覆盖吧

- 鸣潮爆料吧 / 鸣潮内鬼吧（topic=leak，爆料真伪判定）
- 鸣潮强度吧（topic=strength，强度/配队/抽卡分析判定）
- 绝区零内鬼吧 / 绝区零爆料吧（topic=leak）
- 绝区零强度吧（topic=strength）
- （后续可在 config/sources.yaml 中添加更多）

## Web 仪表盘

Windows 侧运行 `python -m src.web` 后访问 http://127.0.0.1:8787 ，可视化查看爆料/强度/删帖/抓取日志。
- 已读/未读跟踪：点开详情自动已读，首页默认只看未读（threads.read_at）
- 🔥 智能推荐：未读情报按 置信度+新鲜度+删帖 加权排序
- 「立即抓取」按钮：页面上直接触发一轮完整管线

## 定时自主抓取

openclaw cron 任务「游戏雷达自动抓取」每 3 小时（北京时间 8-23 点）自动跑一轮管线。
查看：`openclaw cron list`；手动触发：`openclaw cron run <id>`。
