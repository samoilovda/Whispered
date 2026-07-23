# Windows packaging

Run from Windows 11 x64 with Python 3.11:

```powershell
.\setup-windows.ps1
.\packaging\windows\build-windows.ps1
```

The build produces an unsigned `onedir` package and ZIP under `dist/`.
Install Inno Setup to also produce the per-user installer. Release signing is
deliberately not performed locally: sign only in the protected release workflow.
