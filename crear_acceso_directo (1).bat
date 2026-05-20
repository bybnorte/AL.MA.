@echo off
title AL.MA - Crear acceso directo

echo Creando acceso directo de AL.MA en el escritorio...

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $sc = $ws.CreateShortcut($desktop + '\AL.MA.lnk'); $sc.TargetPath = 'C:\Users\Borybor\Dropbox\ALMA\ALMA.vbs'; $sc.WorkingDirectory = 'C:\Users\Borybor\Dropbox\ALMA'; $sc.Description = 'AL.MA Asistente Personal'; $sc.IconLocation = 'C:\Users\Borybor\Dropbox\ALMA\alma_icon.ico'; $sc.Save(); Write-Host 'Listo:' $desktop"

echo.
echo Revisa el Escritorio, deberia aparecer el icono de AL.MA.
echo.
pause
