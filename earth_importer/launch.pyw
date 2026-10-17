"""Lanceur sans console : double-clic (Windows) ou EarthImporter.bat.

Sans console les erreurs seraient invisibles : elles vont dans earth_importer.log,
et une boite de dialogue s'affiche si le lancement echoue.
"""
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "earth_importer.log"
sys.stdout = sys.stderr = open(LOG, "w", encoding="utf-8", buffering=1)
sys.path.insert(0, str(HERE))
print(f"Python {sys.version} ({sys.executable})")

try:
    import app
    app.main()
except SystemExit:
    pass
except BaseException as e:
    traceback.print_exc()
    hint = ""
    if isinstance(e, ModuleNotFoundError):
        hint = (f"\n\nModule manquant pour ce Python ({sys.executable}).\n"
                f"Installe les dependances avec :\n\"{sys.executable}\" -m pip install -r requirements.txt")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"{e}{hint}\n\nDetails : {LOG}", "Earth Importer - erreur", 0x10)
    except Exception:
        pass
