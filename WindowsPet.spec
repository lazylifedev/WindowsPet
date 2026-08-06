from PyInstaller.utils.hooks import collect_submodules
block_cipher = None
a = Analysis(['src/windows_pet/main.py'], pathex=['src'], datas=[('assets', 'assets')], hiddenimports=collect_submodules('windows_pet'), cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='WindowsPet', console=False)
