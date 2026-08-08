from pathlib import Path

PROJECT_ROOT = Path(SPEC).resolve().parent
block_cipher = None
a = Analysis(
    [str(PROJECT_ROOT / "windows_pet_elevation_broker.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    datas=[],
    hiddenimports=[
        "windows_pet.elevation.models",
        "windows_pet.elevation.envelope",
        "windows_pet.elevation.validation",
        "windows_pet.elevation.broker",
    ],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="WindowsPet.ElevationBroker", console=True)
