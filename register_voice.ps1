$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $python voice_agent.py --register
