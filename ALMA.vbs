Dim WshShell
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "python C:\Users\Borybor\Dropbox\ALMA\alma.py", 0, False
WScript.Sleep 2500
WshShell.Run "http://localhost:5000"
