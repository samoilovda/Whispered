# Standalone build, packaging and release

Read before changing `build.py`, `main.py` startup, `packaging/`, `appimage/`,
`.github/workflows/release.yml` / `linux-package.yml`, the version, or signing.

## macOS app (`build.py`)

- Run with the venv: `.venv/bin/python build.py` → `dist/Whispered.app`
  (`--no-libs` builds the .app only; `--bundle-libs --no-libs` is the
  self-contained release build).
- The whisper stack (pywhispercpp, libwhisper/libggml and pywhispercpp's
  pure-python deps) is deliberately NOT bundled in dev builds: `build.py`
  deploys it to `~/Library/Application Support/Whispered/lib`, and
  `main.py::_setup_frozen_runtime()` puts that dir on `sys.path`. With
  `--bundle-libs` it goes to `Contents/Resources/whisper-runtime`; the external
  dir wins when both exist.
- Transcription children are spawned by re-executing the frozen binary:
  `multiprocessing.freeze_support()` in `main.py` must run after
  `_setup_frozen_runtime()` (children import pywhispercpp from the external lib
  dir) and before any Qt import.
- Standalone driver scripts that use `multiprocessing` spawn must have an
  `if __name__ == "__main__"` guard, or the child re-runs the script.
- Signing: `build.py` uses the self-signed "Whispered Local Signing"
  certificate from the login keychain when it exists (create it once with
  `tools/create_signing_cert.sh`) or the identity in `WHISPERED_SIGN_IDENTITY`,
  else ad-hoc. Ad-hoc builds change identity on every rebuild, so macOS asks
  for the keychain password again to read the stored YouTube login.

## Release

- Releases go only through the manual `workflow_dispatch` workflow
  `.github/workflows/release.yml` (`gh workflow run release.yml -f version=vX.Y.Z`);
  it builds macOS (`build.py --bundle-libs --no-libs`), Windows
  (`packaging/windows/Whispered.windows.spec`) and Linux, and by default
  creates a draft GitHub Release for a human to publish.
- The tag must match `[project].version` in `pyproject.toml` (mirrored in
  `version.py`), checked by `tools/check_release_version.py`;
  `tests/test_version.py` guards the packaging files.
- Release notes: `docs/release-notes/<version>.md`, bilingual EN+RU, copied
  from `TEMPLATE.md`.
- Linux (`linux-package.yml`, Fedora container) is a preview channel: its
  failure must not block the macOS/Windows release. It can run alone to check
  the Linux build without a release.
- Builds are unsigned and not notarized; release notes carry the Gatekeeper /
  SmartScreen workaround.
