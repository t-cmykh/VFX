@echo off
rem Meme lancement mais avec la console visible, pour lire les erreurs
cd /d "%~dp0"
where python >nul 2>nul && (python app.py) || (py app.py)
pause
