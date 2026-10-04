@echo off
cd /d "%~dp0"
python img2pbr_gui.py
if errorlevel 1 pause
