# Cookie 导出说明

把贴吧登录态导出成 Playwright 能用的 JSON 格式，保存为 `cookies/tieba.json`。

## 推荐方式：Cookie-Editor 扩展

1. Chrome / Edge 装 [Cookie-Editor](https://cookie-editor.com/)。
2. **强烈建议用小号**登录 `https://tieba.baidu.com`（高频抓取有封号风险）。
3. 打开 Cookie-Editor，点 Export → "Export as JSON"。
4. 把导出的 JSON 保存到 `cookies/tieba.json`。

## 格式

Playwright 接受的 cookie 数组形如：

```json
[
  {
    "name": "BDUSS",
    "value": "xxx...",
    "domain": ".baidu.com",
    "path": "/",
    "expires": 1791000000,
    "httpOnly": true,
    "secure": true,
    "sameSite": "Lax"
  }
]
```

抓取器会自动把 `sameSite` 大小写规范化（None/Lax/Strict）。如果导出工具用了别的字段名（如 `expirationDate`），抓取器也会做兼容。
