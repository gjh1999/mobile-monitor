# Notify the local desktop and let Codex open its original approval prompt.
$ErrorActionPreference = 'Stop'
try {
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
    $text = [Console]::In.ReadToEnd()
    if ($text.Length -gt 1048576) { exit 0 }
    $event = ConvertFrom-Json -InputObject $text
    if ($event.hook_event_name -ne 'PermissionRequest') { exit 0 }

    $project = [IO.Path]::GetFileName(([string]$event.cwd).TrimEnd('\', '/'))
    $tool = [string]$event.tool_name
    $thread = [string]$event.session_id
    $row = @{
        project = $project.Substring(0, [Math]::Min(160, $project.Length))
        tool = $tool.Substring(0, [Math]::Min(120, $tool.Length))
        thread = $thread.Substring(0, [Math]::Min(80, $thread.Length))
    }
    [void][IO.Directory]::CreateDirectory($inbox)
    $name = Join-Path $inbox ([Guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($name + '.tmp', (ConvertTo-Json -InputObject $row -Compress), [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($name + '.tmp', $name + '.json')

    # Give the running desktop first chance to consume the notice. The new process
    # has its own console handles so the Codex hook can return immediately.
    $popup = {
        Start-Sleep -Milliseconds 2300
        if (![IO.File]::Exists($notice)) { exit 0 }
        Add-Type -AssemblyName System.Windows.Forms
        try { [IO.File]::Move($notice, $notice + '.shown') } catch { exit 0 }
        try { $row = ConvertFrom-Json ([IO.File]::ReadAllText($notice + '.shown')) }
        finally { [IO.File]::Delete($notice + '.shown') }
        $owner = New-Object Windows.Forms.Form -Property @{TopMost=$true; Opacity=0; ShowInTaskbar=$false}
        try {
            $owner.Show()
            $message = $row.project + ' · ' + $row.tool + "`n请在发起请求的 Codex 窗口确认。"
            [void][Windows.Forms.MessageBox]::Show($owner, $message, 'Codex 等待审批', 'OK', 'Information')
        } finally { $owner.Dispose() }
    }
    $child = '$notice = ''' + ($name + '.json').Replace("'", "''") + "'`n" + $popup.ToString()
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($child))
    $start = New-Object Diagnostics.ProcessStartInfo -Property @{
        FileName = Join-Path $PSHOME 'powershell.exe'
        Arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $encoded
        WindowStyle = 'Hidden'
        UseShellExecute = $true
    }
    [Diagnostics.Process]::Start($start).Dispose()
} catch {
    # The reminder is optional. An unavailable inbox must leave native approval available.
}
exit 0
