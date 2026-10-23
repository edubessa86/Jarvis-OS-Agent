$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$InstallRoot = Join-Path $env:LOCALAPPDATA "Jarvis-OS"
$RepoUrl = "https://github.com/edubessa86/Jarvis-OS-Agent.git"
try {
  Write-Host "==> Verificando espaço livre..."
  $drive = Get-PSDrive C
  $freeGB = [math]::Round($drive.Free / 1GB, 1)
  Write-Host "Espaço livre no C: $freeGB GB"
  if ($drive.Free -lt 12GB) { throw "São necessários pelo menos 12 GB livres. Libere espaço e execute novamente." }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "winget não encontrado. Instale App Installer pela Microsoft Store." }
  Write-Host "==> Verificando Git..."
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
    $env:Path += ";$env:ProgramFiles\Git\cmd"
  }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git não encontrado após instalação. Reinicie o Windows e tente novamente." }
  Write-Host "==> Verificando Python 3.11..."
  $pythonExe = $null
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try { $pythonExe = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1) } catch {}
  }
  if (-not $pythonExe -or -not (Test-Path $pythonExe)) {
    winget install --id Python.Python.3.11 -e --source winget --accept-package-agreements --accept-source-agreements --silent
    $candidates = @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe","$env:ProgramFiles\Python311\python.exe")
    foreach ($candidate in $candidates) { if (Test-Path $candidate) { $pythonExe = $candidate; break } }
    if (-not $pythonExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
      try { $pythonExe = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1) } catch {}
    }
  }
  if (-not $pythonExe -or -not (Test-Path $pythonExe)) { throw "Python 3.11 não foi localizado. Reinicie o Windows e execute novamente." }
  Write-Host "Python: $pythonExe"
  Write-Host "==> Baixando o repositório Jarvis..."
  if (Test-Path $InstallRoot) {
    if (Test-Path (Join-Path $InstallRoot ".git")) {
      git -C $InstallRoot pull --ff-only
      if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar a pasta existente." }
    } else { throw "A pasta $InstallRoot já existe e não é um repositório Git. Renomeie-a e tente novamente." }
  } else {
    git clone --depth 1 $RepoUrl $InstallRoot
    if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar o repositório." }
  }
  Write-Host "==> Criando ambiente Python..."
  $venvPython = Join-Path $InstallRoot ".venv\Scripts\python.exe"
  if (-not (Test-Path $venvPython)) {
    & $pythonExe -m venv (Join-Path $InstallRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar ambiente virtual." }
  }
  & $venvPython -m pip install --upgrade pip setuptools wheel
  if ($LASTEXITCODE -ne 0) { throw "Falha ao preparar pip." }
  Write-Host "==> Instalando dependências. Isso pode levar bastante tempo..."
  & $venvPython -m pip install --no-cache-dir --timeout 120 --retries 10 -r (Join-Path $InstallRoot "requirements.txt")
  if ($LASTEXITCODE -ne 0) { throw "Falha nas dependências. Verifique internet e espaço livre e execute o instalador novamente." }
  Write-Host "==> Criando atalhos..."
  $launcher = Join-Path $InstallRoot "Iniciar-Jarvis.cmd"
  $launcherContent = '@echo off' + [Environment]::NewLine + 'cd /d "' + $InstallRoot + '"' + [Environment]::NewLine + '"' + $venvPython + '" "' + $InstallRoot + '\main.py"'
  Set-Content -Path $launcher -Value $launcherContent -Encoding ASCII
  $desktop = [Environment]::GetFolderPath("Desktop")
  $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
  $shell = New-Object -ComObject WScript.Shell
  foreach ($path in @((Join-Path $desktop "Jarvis OS Agent.lnk"),(Join-Path $startMenu "Jarvis OS Agent.lnk"))) {
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $launcher
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.Description = "Iniciar Jarvis OS Agent"
    $shortcut.Save()
  }
  Write-Host "INSTALAÇÃO CONCLUÍDA!" -ForegroundColor Green
  Write-Host "Pasta: $InstallRoot"
  Write-Host "Painéis locais: http://127.0.0.1:8080 e http://127.0.0.1:8090"
} catch {
  Write-Host "ERRO NA INSTALAÇÃO: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Corrija o problema e execute novamente. Pressione Enter para fechar."
  Read-Host
  exit 1
}