"""PyInstaller entry point: execute the application as a package."""

from windows_pet.main import main


if __name__ == "__main__":
    raise SystemExit(main())
