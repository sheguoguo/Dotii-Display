Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
bridgeFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
projectFolder = fileSystem.GetParentFolderName(bridgeFolder)
workspaceFolder = fileSystem.GetParentFolderName(projectFolder)

' Pick a real interpreter. A bare "pythonw.exe" on PATH is often the Microsoft
' Store stub, which exits silently and leaves the dashboard blank, so try the
' known virtualenvs first and only fall back to PATH as a last resort.
candidates = Array( _
  workspaceFolder & "\.venv-web\Scripts\pythonw.exe", _
  projectFolder & "\.codex\packaging-venv\Scripts\pythonw.exe", _
  workspaceFolder & "\.venv-esptool\Scripts\pythonw.exe" _
)

pythonCommand = ""
For Each candidate In candidates
  If pythonCommand = "" Then
    If fileSystem.FileExists(candidate) Then
      pythonCommand = """" & candidate & """"
    End If
  End If
Next

If pythonCommand = "" Then
  pythonCommand = "pythonw.exe"
End If

command = pythonCommand & " -B """ & bridgeFolder & "\bridge_app.py"" --open-dashboard"
If WScript.Arguments.Named.Exists("debug") Then
  ' Run with a visible console to see the traceback: wscript 启动.vbs /debug
  shell.Run "cmd /k " & command, 1, False
Else
  shell.Run command, 0, False
End If
