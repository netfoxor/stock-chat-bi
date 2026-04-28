#Requires -Version 5.1
<#
.SYNOPSIS
  开发机（Windows）：构建 nanobot 镜像 → docker save → gzip 打包离线包
  输出：deploy/dist/nanobot-image.tar.gz

.DESCRIPTION
  前提：nanobot/data/stock_prices_history.db 已就绪（仓库约定内置）。
  如不存在，脚本会尝试从 ../qwen-agent/stock_prices_history.db 复制。

.EXAMPLE
  cd nanobot
  powershell -ExecutionPolicy Bypass -File deploy\build_and_save.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RootDir

$Image     = "nanobot-app:latest"
$OutDir    = Join-Path $RootDir "deploy\dist"
# 打包时间戳：方便同目录下留历史版本，便于回滚 / 对比大小
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$TarPath   = Join-Path $OutDir "nanobot-image-$Timestamp.tar"
$GzPath    = Join-Path $OutDir "nanobot-image-$Timestamp.tar.gz"

function Write-Stage([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# --- 0. 前置检查 ---
Write-Stage "检查 docker"
$dockerVersion = docker version --format '{{.Server.Version}}' 2>$null
if (-not $dockerVersion) {
    throw "未检测到 docker，请先安装 Docker Desktop 并启动引擎"
}
Write-Host "    docker engine: $dockerVersion"

# --- 1. 校验 data/ 初始 DB ---
Write-Stage "校验内置数据库"
$DbPath = Join-Path $RootDir "data\stock_prices_history.db"

if (-not (Test-Path $DbPath)) {
    $SrcDb = Join-Path $RootDir "..\qwen-agent\stock_prices_history.db"
    if (Test-Path $SrcDb) {
        Write-Host "    data/ 下无 DB，从 ../qwen-agent 复制一份"
        New-Item -ItemType Directory -Force -Path (Split-Path $DbPath) | Out-Null
        Copy-Item -Force $SrcDb $DbPath
    } else {
        throw "未找到 $DbPath，也未在 $SrcDb 找到源文件；请自行放入 nanobot/data/"
    }
}

$dbMB = [math]::Round((Get-Item $DbPath).Length / 1MB, 2)
Write-Host "    数据库：$DbPath  ($dbMB MB)"

# --- 2. 确保 base image 已在本地 ---
Write-Stage "检查 base image"
$BaseImage = "python:3.11-slim-bookworm"
# 用 `docker images -q` 查询镜像 ID，没有就输出空，不会走 stderr 触发 PowerShell 红字误报
$imgId = (docker images -q $BaseImage 2>$null) | Out-String
if ([string]::IsNullOrWhiteSpace($imgId)) {
    Write-Host "    本地无 $BaseImage，执行 docker pull（首次拉取会稍慢）..."
    docker pull $BaseImage
    if ($LASTEXITCODE -ne 0) {
        throw @"
docker pull $BaseImage 失败。
常见原因：Docker Desktop 的 registry mirror 或 proxy 配置与你的代理冲突。
修复见 deploy/README.md 的『常见问题：docker pull 拉不到 base image』一节。
"@
    }
} else {
    Write-Host "    $BaseImage 已在本地（image id: $($imgId.Trim())），跳过 pull"
}

# --- 3. docker build（--pull=false 避免每次联 registry 查 metadata） ---
# 注意：`--pull` 在新版 buildx 里只接 true/false；旧版 docker build 用 never。
Write-Stage "docker build $Image"
docker build --pull=false -t $Image -f Dockerfile .
if ($LASTEXITCODE -ne 0) { throw "docker build 失败" }

# --- 4. docker save -> tar ---
Write-Stage "docker save -> tar"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $TarPath) { Remove-Item -Force $TarPath }
if (Test-Path $GzPath)  { Remove-Item -Force $GzPath }

docker save -o $TarPath $Image
if ($LASTEXITCODE -ne 0) { throw "docker save 失败" }

$tarMB = [math]::Round((Get-Item $TarPath).Length / 1MB, 2)
Write-Host "    tar 大小：$tarMB MB"

# --- 5. gzip 压缩（纯 .NET，不依赖外部 gzip） ---
Write-Stage "gzip 压缩"
Add-Type -AssemblyName "System.IO.Compression"

$inStream  = [System.IO.File]::OpenRead($TarPath)
$outStream = [System.IO.File]::Create($GzPath)
$gzStream  = New-Object System.IO.Compression.GZipStream(
    $outStream, [System.IO.Compression.CompressionLevel]::Optimal)
try {
    $buffer = New-Object byte[] (4MB)
    while (($read = $inStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $gzStream.Write($buffer, 0, $read)
    }
} finally {
    $gzStream.Dispose()
    $outStream.Dispose()
    $inStream.Dispose()
}
Remove-Item -Force $TarPath

$gzMB = [math]::Round((Get-Item $GzPath).Length / 1MB, 2)
Write-Host "    gz 大小：$gzMB MB"

# --- 6. 汇总提示 ---
Write-Stage "打包完成"
Write-Host ""
Write-Host "离线包：" -ForegroundColor Green
Write-Host "  $GzPath"
Write-Host ""
$GzFileName = Split-Path -Leaf $GzPath
Write-Host "传到 1Panel 服务器 /opt/nanobot/ 的文件：" -ForegroundColor Green
Write-Host "  1. deploy/dist/$GzFileName    (镜像，含 DB)"
Write-Host "  2. docker-compose.yml                             (编排文件)"
Write-Host "  3. .env                                           (从 .env.example 复制并填 DASHSCOPE_API_KEY)"
Write-Host ""
Write-Host "然后在 1Panel UI：容器 → 镜像 → 导入镜像，再 容器 → 编排 → 创建编排（本地目录 /opt/nanobot）"
Write-Host "完整步骤见 deploy/README.md"
