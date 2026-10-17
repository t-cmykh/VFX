@echo off
rem Lance Earth Importer sans terminal. En cas de probleme : EarthImporter_debug.bat
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw launch.pyw) || (start "" pyw launch.pyw)
