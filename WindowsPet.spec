from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

PROJECT_ROOT = Path(SPEC).resolve().parent
ANIMATIONS = PROJECT_ROOT / "assets" / "animations"
animation_datas = [(str(path), str(Path("assets/animations") / path.relative_to(ANIMATIONS).parent))
                   for path in ANIMATIONS.rglob("*") if path.is_file()]
block_cipher = None
a = Analysis([str(PROJECT_ROOT / 'windows_pet_launcher.py')], pathex=[str(PROJECT_ROOT / 'src')], datas=animation_datas, hiddenimports=collect_submodules('windows_pet'), cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='WindowsPet', console=False)
