@echo off
cd /d "%~dp0"
python img2hdri_gui.py
if errorlevel 1 pause
