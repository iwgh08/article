# 매일 08:00, 20:00에 news_brief.py를 실행하는 작업을 등록한다.
# 실행: 이 폴더에서 PowerShell로  .\register_task.ps1
#  (실행 정책 오류가 나면  powershell -ExecutionPolicy Bypass -File .\register_task.ps1)

$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$script = Join-Path $dir 'news_brief.py'

# 콘솔 창이 뜨지 않도록 pythonw.exe 사용
$python = (& py -3 -c "import sys; print(sys.executable)").Trim()
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $dir
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

foreach ($t in @(@{Name = 'AI 뉴스 브리핑 (오전)'; At = '08:00'}, @{Name = 'AI 뉴스 브리핑 (오후)'; At = '20:00'})) {
    $trigger = New-ScheduledTaskTrigger -Daily -At $t.At
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings `
        -Description 'AI 뉴스를 요약해 PDF로 Gmail 발송' -Force | Out-Null
    Write-Host "등록 완료: $($t.Name) - 매일 $($t.At)"
}
Write-Host "실행 파일: $pythonw"
