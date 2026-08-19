' FILE NOTE
' - Mục đích: Desktop launcher không mở cửa sổ console đen cho Local AI Hub (hỗ trợ dynamic pythonw resolution)
' - Liên kết trực tiếp: src/app/launcher.py, distribution/installer.iss, scripts/update_managed_shortcuts.ps1
' - Vùng ảnh hưởng khi sửa: Khởi động ứng dụng từ shortcut desktop/start menu
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strRoot = objFSO.GetParentFolderName(WScript.ScriptFullName)

strPythonw = ""
If objFSO.FileExists(strRoot & "\Environments\hub\Scripts\pythonw.exe") Then
    strPythonw = strRoot & "\Environments\hub\Scripts\pythonw.exe"
ElseIf objFSO.FileExists(strRoot & "\runtime\bootstrap-python\pythonw.exe") Then
    strPythonw = strRoot & "\runtime\bootstrap-python\pythonw.exe"
Else
    strPythonw = "pythonw.exe"
End If

strCmd = chr(34) & strPythonw & chr(34) & " -m src.app.launcher"
objShell.CurrentDirectory = strRoot
objShell.Run strCmd, 0, False
