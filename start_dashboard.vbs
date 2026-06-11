' 游戏雷达仪表盘启动器（无窗口）。开机自启用，也可手动双击。
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "C:\Users\streamax\Desktop\龙虾学术"
ws.Run """C:\Users\streamax\Desktop\龙虾学术\.venv\Scripts\pythonw.exe"" -m src.web", 0, False

