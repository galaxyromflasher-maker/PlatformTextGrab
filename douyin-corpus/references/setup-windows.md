# Windows 环境搭建

所有安装都是**用户级**，不需要管理员权限。

## 不需要的东西（别装）

| 别装 | 原因 |
|---|---|
| Node.js | `capture.js` 是浏览器页面脚本，不是 Node 程序 |
| Chromium | `browser.py` 复用本机 Edge（`channel="msedge"`） |
| `playwright install chromium` | 同上，白下 118 MB |

## Python（必需）

如果 `python --version` 报 "Python was not found"，可能是 Windows 商店的占位别名。排查：

```powershell
Get-Command python* | Select Name,Source
```

如果命中 `WindowsApps` 目录，就是假的。安装真实 Python：

```powershell
# 华为云镜像，27 MB，几秒下完
curl.exe -sL -o "$env:TEMP\python.exe" `
  "https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe"

# 用户级静默安装：不弹窗、自动前置 PATH
Start-Process "$env:TEMP\python.exe" -Wait -ArgumentList @(
  "/quiet","InstallAllUsers=0","PrependPath=1","Include_pip=1","Include_test=0"
)
```

装完**重启终端**，再验证 `python --version`。

## Python 依赖（必需）

```powershell
pip install -r requirements.txt
```

## ffmpeg / ffprobe（必需，两个都要）

如果 `ffmpeg -version` 报"不是内部或外部命令"，用 npmmirror 镜像下载（约 20 MB/s）：

```powershell
$bin  = "$env:LOCALAPPDATA\Programs\ffmpeg\bin"
$base = "https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.1.1"
New-Item -ItemType Directory -Path $bin -Force | Out-Null
curl.exe -sL -o "$bin\ffmpeg.exe"  "$base/ffmpeg-win32-x64"
curl.exe -sL -o "$bin\ffprobe.exe" "$base/ffprobe-win32-x64"
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$bin", "User")
```

装完**重启终端**。验证：`ffmpeg -version` 和 `ffprobe -version`。

## Playwright（必需）

```powershell
pip install playwright
# 不要执行 playwright install chromium！
```

## 常见问题

**装好了但当前终端找不到命令**：Windows 的 PATH 是进程启动时读的。装完必须**开新终端**。

**下载慢或断流**：先怀疑源，不要怀疑网。国内镜像比代理快十倍。

**pip 警告 "scripts not on PATH"**：这是警告不是失败，忽略即可。
