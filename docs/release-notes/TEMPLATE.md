<!--
Release-notes template. Copy to docs/release-notes/vX.Y.Z.md, fill in, and
the Release workflow (.github/workflows/release.yml) uses it as the GitHub
Release body. Keep both language sections — English first, Russian after
the divider. If this file is missing for a tag, GitHub auto-generates the
body from merged PRs instead.
-->

## Whispered vX.Y.Z

<one-paragraph summary of what this release is>

### Downloads

| Platform | File | Notes |
|---|---|---|
| macOS (Apple Silicon) | `Whispered-vX.Y.Z-macos-arm64.zip` | macOS 13+; unsigned — see below |
| Windows 11 (x64) | `Whispered-vX.Y.Z-windows-x64.zip` | unsigned preview — see below |

Verify your download against `SHA256SUMS.txt`:

```bash
shasum -a 256 -c SHA256SUMS.txt      # macOS / Linux
```
```powershell
(Get-FileHash Whispered-vX.Y.Z-windows-x64.zip -Algorithm SHA256).Hash
```

### Highlights

- ...

### Known limitations

- ...

### Installing on macOS (unsigned build)

These builds are **not signed with an Apple Developer ID and not notarized**
($99/year Apple Developer Program). Gatekeeper will block the first launch.
This is expected — the app is not doing anything unusual, it simply has no
paid signature.

1. Unzip and move `Whispered.app` to `/Applications`.
2. Right-click `Whispered.app` → **Open** → **Open** in the dialog. (A plain
   double-click only offers "Move to Trash".)
3. If macOS still refuses, run once in Terminal:
   ```bash
   xattr -dr com.apple.quarantine /Applications/Whispered.app
   ```

The whisper transcription engine ships **inside** the app — no extra
install step. Model weights download on first use.

### Installing on Windows (unsigned preview)

No code-signing certificate yet, so SmartScreen shows
"Windows protected your PC". Click **More info** → **Run anyway**. Unzip
anywhere and run `Whispered.exe`.

---

## Whispered vX.Y.Z (Русский)

<краткое описание релиза одним абзацем>

### Загрузки

| Платформа | Файл | Примечания |
|---|---|---|
| macOS (Apple Silicon) | `Whispered-vX.Y.Z-macos-arm64.zip` | macOS 13+; без подписи — см. ниже |
| Windows 11 (x64) | `Whispered-vX.Y.Z-windows-x64.zip` | превью без подписи — см. ниже |

Проверьте загрузку по `SHA256SUMS.txt`:

```bash
shasum -a 256 -c SHA256SUMS.txt      # macOS / Linux
```
```powershell
(Get-FileHash Whispered-vX.Y.Z-windows-x64.zip -Algorithm SHA256).Hash
```

### Главное

- ...

### Известные ограничения

- ...

### Установка на macOS (сборка без подписи)

Сборки **не подписаны Apple Developer ID и не нотаризованы** (программа
Apple Developer — $99/год). При первом запуске Gatekeeper заблокирует
приложение. Это ожидаемо: приложение не делает ничего необычного, просто
у него нет платной подписи.

1. Распакуйте и переместите `Whispered.app` в `/Applications`.
2. Правый клик по `Whispered.app` → **Открыть** → **Открыть** в диалоге.
   (Обычный двойной клик предложит только «Переместить в корзину».)
3. Если macOS всё ещё не пускает, один раз выполните в Терминале:
   ```bash
   xattr -dr com.apple.quarantine /Applications/Whispered.app
   ```

Движок транскрипции whisper поставляется **внутри** приложения — отдельная
установка не нужна. Веса моделей скачиваются при первом использовании.

### Установка на Windows (превью без подписи)

Сертификата подписи кода пока нет, поэтому SmartScreen покажет
«Система Windows защитила ваш компьютер». Нажмите **Подробнее** →
**Выполнить в любом случае**. Распакуйте архив и запустите `Whispered.exe`.
