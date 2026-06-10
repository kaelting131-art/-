# 游戏雷达 v0

抓取贴吧爆料/内鬼吧，建快照库。LLM 判定、信誉分、Web 仪表盘后续接。

当前覆盖：
- 鸣潮爆料吧
- 鸣潮内鬼吧

## 安装

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 准备 cookie

见 `cookies/README.md`。简单说：用浏览器装 "Cookie-Editor" 之类的扩展，登录贴吧（建议小号），导出 baidu.com 域的 cookie 为 JSON，保存到 `cookies/tieba.json`。

## 运行

```powershell
python -m src.main           # 单次抓取
python -m src.main --loop    # 循环抓取（默认每 10 分钟一轮）
```

数据落在 `data/radar.db`（SQLite）。

## 设计要点

- **快照优先**：每次抓到的帖子先全量入库，含原始 HTML。删了也留得住。
- **删帖检测**：每轮对最近 N 小时内见过的帖子做存活检查，标记 `deleted_at`。
- **两段过滤**（后续）：先关键词粗筛 → LLM (DeepSeek V4) 判真伪/去重。
- **信誉分**（后续）：按发帖人 ID 累积。
