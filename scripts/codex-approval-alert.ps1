# Notify the local desktop and let Codex open its original approval prompt.
$ErrorActionPreference = 'Stop'

function Read-ApprovalDecision($request) {
    $command = $request.tool_input.command
    if ($request.tool_name -notin @('Bash', 'apply_patch', 'Edit', 'Write') -or
        $request.session_id -notmatch '^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$' -or
        [string]::IsNullOrWhiteSpace($request.turn_id) -or
        !($command -is [string]) -or [string]::IsNullOrWhiteSpace($command) -or $command.Length -gt 65536 -or
        $command.Replace("`r", '').Replace("`n", '').Replace("`t", '') -match '[\p{Cc}\p{Cf}]') { return }

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form = New-Object Windows.Forms.Form -Property @{
        Text='澜汐 · Codex 审批'; StartPosition='CenterScreen'; TopMost=$true
        ClientSize=[Drawing.Size]::new(700,430); MinimumSize=[Drawing.Size]::new(570,320)
    }
    $timer = New-Object Windows.Forms.Timer -Property @{Interval=90000}
    try {
        $layout = New-Object Windows.Forms.TableLayoutPanel -Property @{Dock='Fill'; ColumnCount=1; RowCount=3; Padding=[Windows.Forms.Padding]::new(14)}
        $layout.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize')) | Out-Null
        $layout.RowStyles.Add([Windows.Forms.RowStyle]::new('Percent',100)) | Out-Null
        $layout.RowStyles.Add([Windows.Forms.RowStyle]::new('AutoSize')) | Out-Null
        $form.Controls.Add($layout)
        $folder = [string]$request.cwd
        $heading = New-Object Windows.Forms.Label -Property @{
            AutoSize=$true; UseMnemonic=$false; MaximumSize=[Drawing.Size]::new(640,0)
            Text=([string]$request.tool_name + " · " + $folder + "`n是否允许这一次操作？")
        }
        $layout.Controls.Add($heading,0,0)
        $extra = [ordered]@{}
        foreach ($property in $request.tool_input.PSObject.Properties) {
            if ($property.Name -ne 'command') { $extra[$property.Name] = $property.Value }
        }
        $preview = $command
        if ($extra.Count -gt 0) {
            $preview += "`r`n`r`n附加请求详情`r`n" + (ConvertTo-Json -InputObject $extra -Depth 30)
        }
        $detail = New-Object Windows.Forms.RichTextBox -Property @{
            Dock='Fill'; ReadOnly=$true; WordWrap=$false; DetectUrls=$false; Text=$preview
            Font=[Drawing.Font]::new('Consolas',10)
        }
        $layout.Controls.Add($detail,0,1)

        $buttons = New-Object Windows.Forms.FlowLayoutPanel -Property @{Dock='Fill'; AutoSize=$true; FlowDirection='RightToLeft'; WrapContents=$false}
        $layout.Controls.Add($buttons,0,2)
        $defer = New-Object Windows.Forms.Button -Property @{Text='回到 Codex'; AutoSize=$true; DialogResult='Cancel'}
        $deny = New-Object Windows.Forms.Button -Property @{Text='拒绝'; AutoSize=$true; DialogResult='No'}
        $allow = New-Object Windows.Forms.Button -Property @{Text='接受一次'; AutoSize=$true; DialogResult='Yes'}
        $buttons.Controls.AddRange(@($defer, $deny, $allow))
        $form.CancelButton = $defer
        $form.ActiveControl = $defer
        $form.Add_Shown({ $form.Activate(); $defer.Focus() | Out-Null })
        $timer.Add_Tick({ $form.Close() })
        $timer.Start()
        switch ($form.ShowDialog()) {
            'Yes' { return 'allow' }
            'No' { return 'deny' }
            default { return 'defer' }
        }
    } finally {
        $timer.Dispose()
        $form.Dispose()
    }
}

try {
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
    $text = [Console]::In.ReadToEnd()
    if ($text.Length -gt 1048576) { exit 0 }
    $event = ConvertFrom-Json -InputObject $text
    if ($event.hook_event_name -ne 'PermissionRequest') { exit 0 }

    $decision = Read-ApprovalDecision $event
    if ($decision -eq 'allow' -or $decision -eq 'deny') {
        $choice = @{behavior=$decision}
        if ($decision -eq 'deny') { $choice.message = '用户在澜汐拒绝了本次操作。' }
        $answer = @{hookSpecificOutput=@{hookEventName='PermissionRequest'; decision=$choice}}
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        [Console]::WriteLine((ConvertTo-Json -InputObject $answer -Depth 4 -Compress))
        exit 0
    }
    if ($decision -eq 'defer') { exit 0 }

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
