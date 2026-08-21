' FILE NOTE
' - Mục đích: Desktop launcher không mở cửa sổ console đen cho Local AI Hub (hỗ trợ dynamic pythonw resolution)
' - Liên kết trực tiếp: src/app/launcher.py, distribution/installer.iss, scripts/update_managed_shortcuts.ps1
' - Vùng ảnh hưởng khi sửa: Khởi động ứng dụng từ shortcut desktop/start menu
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strRoot = objFSO.GetParentFolderName(WScript.ScriptFullName)

strResolver = strRoot & "\scripts\resolve_core_runtime.ps1"
strPowerShell = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & chr(34) & strResolver & chr(34) & " -Mode Pythonw"
Set resolverProcess = objShell.Exec(strPowerShell)
strPythonw = resolverProcess.StdOut.ReadAll
' PowerShell emits a line terminator after the resolved executable.  WScript's
' Trim does not remove CR/LF reliably, so normalize both characters before
' constructing the quoted command line.
strPythonw = Replace(Replace(strPythonw, vbCr, ""), vbLf, "")
strPythonw = Trim(strPythonw)
If strPythonw = "" Then
    MsgBox "Local AI Hub Core runtime chưa sẵn sàng. Hãy chạy scripts\\bootstrap_core.ps1 trước.", vbExclamation, "Local AI Hub"
    WScript.Quit 2
End If

strCmd = chr(34) & strPythonw & chr(34) & " -m src.app.launcher"
objShell.CurrentDirectory = strRoot
objShell.Run strCmd, 0, False
