# План добавления поддержки Windows

Статус: реализация подготовлена; требуется Windows validation gate  
Целевая первая версия: Windows 11 x64, Python 3.11, CPU-транскрипция  
Дата актуализации: 2026-07-23

## Статус реализации

В рабочем дереве подготовлены исходные изменения для W1–W9:

- [x] единые Windows data paths и Windows fallback для отсутствующего
  `LOCALAPPDATA`;
- [x] resolver bundled/PATH FFmpeg и Windows-подсказки;
- [x] mic-only Live UI и запрет macOS system-audio adapter на Windows;
- [x] Windows PowerShell setup/run scripts;
- [x] Windows PyInstaller `onedir`, ZIP, Inno Setup template, version resource
  и Windows icon;
- [x] Windows CI job с package + frozen smoke;
- [x] unit tests для paths, tools, capability и Windows preflight;
- [x] исходный smoke mode `--smoke-test`.

Не завершены и не могут быть подтверждены в macOS workspace:

- [ ] W0: реальный `pywhispercpp` import/transcription/cancel на Windows 11;
- [ ] реальная Windows PyInstaller build и запуск полученного `.exe`;
- [ ] Inno Setup installer в чистой Windows VM;
- [ ] запись, Qt Multimedia и FFmpeg на Windows hardware;
- [ ] Windows CI run после публикации изменений;
- [ ] code signing и полный ручной release gate.

Пока эти пункты не закрыты, Windows нельзя объявлять поддерживаемой релизной
платформой в README или roadmap.

## 1. Цель

Добавить Windows как реально проверяемую платформу Whispered, а не только как
условную ветку в нескольких функциях.

Результатом первой итерации должны стать:

- запуск приложения из исходников в чистом пользовательском профиле Windows;
- работа транскрипции, отмены, истории, записи с микрофона, плеера, экспорта,
  пакетной очереди, Cut/EDL и AI-инструментов;
- воспроизводимая Windows-сборка на самом Windows;
- переносимый ZIP и устанавливаемый пакет;
- Windows-job в CI;
- отдельный ручной release gate;
- честная документация о поддерживаемых и неподдерживаемых возможностях.

## 2. Границы первой версии

### Входит в Windows MVP

- Windows 11 x64;
- Python 3.11 для разработки и сборки;
- CPU-wheel `pywhispercpp`;
- все обычные файловые сценарии;
- запись и Live-транскрипция с микрофона;
- локальный LM Studio;
- OpenAI-совместимый и Anthropic-провайдеры для YouTube-пакета;
- опциональная диаризация после отдельной проверки `torch` +
  `pyannote.audio`;
- `ffmpeg`/`ffprobe` для конвертации и монтажа;
- PyInstaller `onedir`, ZIP и per-user installer.

### Не входит в Windows MVP

- захват системного аудио в Live;
- CUDA, Vulkan, DirectML или другое GPU-ускорение в поставляемой сборке;
- Windows on ARM;
- Microsoft Store / MSIX;
- Windows 10 как заявленная поддерживаемая платформа;
- автоматическое удаление пользовательской истории при uninstall.

Эти ограничения должны быть видимы в UI и документации. Нельзя оставлять
пользователю активную кнопку, которая гарантированно завершится ошибкой.

## 3. Обязательные правила для агента

1. Не собирать Windows-артефакты на macOS или Linux: PyInstaller не является
   кросс-компилятором.
2. До начала изменений сохранить baseline:

   ```bash
   git status --short
   .venv/bin/python -m pytest tests/ -q
   ```

3. Не изменять контракт `Segment` / `TranscriptionResult`.
4. Не переносить транскрипцию обратно в поток: жёсткая отмена через отдельный
   `spawn`-процесс должна сохраниться.
5. Не ломать macOS и Linux. После каждого этапа выполнять текущие тесты,
   `ruff` и headless UI gallery.
6. Не коммитить модели, FFmpeg-бинарники, сертификаты, PFX, API-ключи и
   собранные `.exe`.
7. Делать небольшие завершённые изменения. Каждый этап ниже должен иметь
   тесты и собственный проверяемый критерий готовности.
8. Не менять README-бейдж на Windows и не отмечать roadmap как shipped, пока
   не пройден ручной release gate из раздела 12.

## 4. Что уже готово в кодовой базе

- `main.py` вызывает `multiprocessing.freeze_support()` до импорта Qt.
- Транскрипция уже использует
  `multiprocessing.get_context("spawn")`.
- `TranscriptionWorker` умеет завершать и принудительно убивать дочерний
  процесс.
- `utils.get_models_dir()` и `core.logger._get_log_dir()` уже содержат
  частичные Windows-ветки.
- `ui/settings_dialog.py` открывает папку моделей через `os.startfile()`.
- Большая часть путей передаётся как строки/`pathlib.Path`, а subprocess
  получает список аргументов без shell.
- PyPI публикует CPU wheels `pywhispercpp` для Windows x86-64; пригодность
  выбранной версии всё равно должна быть подтверждена отдельным spike-тестом.

## 5. Известные пробелы

- `core/paths.py` всегда выбирает Linux/XDG-каталог и не знает о Windows.
- `main._setup_frozen_runtime()` целиком описывает macOS `.app` и внешний
  whisper-стек.
- `build.py` и `Whispered.spec` предназначены для macOS.
- нет `setup-windows.ps1`, `run-windows.ps1`, Windows lock-файла и installer;
- CI работает только на Ubuntu;
- подсказки установки FFmpeg не содержат Windows;
- определение NVIDIA GPU может обещать ускорение, хотя поставляемый
  `pywhispercpp` собран только для CPU;
- Live system audio создаёт Unix socket и запускает ScreenCaptureKit-helper;
- текущие сообщения об отмене говорят о `SIGTERM`/`SIGKILL`, чего на Windows
  в таком виде нет;
- нет `.ico`, Windows version resource и метаданных installer;
- не проверены Qt Multimedia plugins/codecs в замороженной сборке;
- не проверены пути с пробелами, кириллицей и длинными именами;
- AppImage-сценарий нельзя использовать как шаблон Windows-пакета: в нём
  неполный список современных root-модулей.

## 6. Порядок реализации

### W0. Compatibility spike на чистом Windows

Цель этапа — снять главный риск до рефакторинга.

На чистой Windows 11 x64 VM:

1. Установить Python 3.11 x64 и Git.
2. Создать venv.
3. Установить `requirements.txt`.
4. Проверить импорт:

   ```powershell
   .\.venv\Scripts\python.exe -c "from pywhispercpp.model import Model; print('ok')"
   ```

5. Скачать модель Tiny или Base штатным API `pywhispercpp`.
6. Транскрибировать короткий WAV из пути:

   ```text
   C:\Users\<user>\Whispered test\Пример записи.wav
   ```

7. Запустить `python main.py`, выполнить транскрипцию и отменить длинный
   запуск.
8. Зафиксировать:

   - точную версию Python;
   - точную версию Windows wheel `pywhispercpp`;
   - наличие и имена DLL;
   - место загрузки модели;
   - время транскрипции;
   - stderr и код возврата при отмене.

Если wheel не работает, не переходить к упаковке. Сначала выбрать рабочую
версию или собрать CPU-wheel в отдельном Windows build job. Не добавлять в
обычный пользовательский installer Visual Studio Build Tools.

Критерий готовности: короткий WAV транскрибируется из исходников, процесс
отменяется, второй экземпляр GUI не запускается.

### W1. Единая платформенная модель путей

Изменить `core/paths.py` так, чтобы он стал единственным источником путей.

Добавить функции:

- `data_dir()`;
- `config_dir()` / `config_path()`;
- `models_dir()`;
- `logs_dir()`;
- `output_dir()`;
- `runtime_dir()`;
- `resource_path(relative)` для source и frozen-режима.

Правила:

- Windows: `%LOCALAPPDATA%\Whispered`;
- macOS: сохранить существующие рабочие пути и миграционное поведение;
- Linux: сохранить XDG и legacy `~/.whisper-fedora`;
- все функции создают только собственный узкий каталог;
- отсутствие `LOCALAPPDATA` имеет безопасный fallback на
  `~/AppData/Local`;
- не смешивать изменяемые данные с каталогом установки.

Перевести на эти функции:

- `config.py`;
- `utils.get_models_dir()` — оставить совместимую обёртку или удалить после
  замены всех вызовов;
- `core/logger.py`;
- `core/history.py`;
- `ui/youtube_panel.py`;
- `core/live/runtime.py`;
- места сохранения output и временных runtime-файлов.

Добавить тесты с monkeypatch платформы и env:

- Windows с `LOCALAPPDATA`;
- Windows без `LOCALAPPDATA`;
- Linux с/без `XDG_DATA_HOME`;
- macOS;
- legacy Linux-каталог;
- Unicode и пробелы;
- запрет записи рядом с frozen `.exe`.

Критерий готовности: config, history, logs, models и output попадают под один
предсказуемый Windows user-data root; существующие macOS/Linux тесты проходят.

### W2. Платформенные возможности вместо определения только железа

Ввести небольшой модуль, например `core/platform_support.py`, который
разделяет:

- целевую ОС;
- найденное железо;
- реально доступный backend текущей сборки;
- доступность микрофона;
- доступность Live system audio;
- доступность FFmpeg;
- source/frozen режим.

Важно: Windows-сборка MVP должна показывать `CPU`, даже если `nvidia-smi`
обнаружил NVIDIA, если bundled `pywhispercpp` не собран с CUDA.

Изменить:

- `utils.detect_gpu()`;
- device badge в `ui/main_window.py`;
- настройки производительности;
- сообщения об ошибках транскрипции;
- Live setup/preflight.

Добавить тесты:

- NVIDIA найдена, но backend CPU-only;
- Windows CPU fallback;
- macOS Metal не меняет поведение;
- Linux CUDA/ROCm сохраняют текущее поведение;
- UI не предлагает недоступный backend.

Критерий готовности: интерфейс описывает фактическую сборку, а не потенциальное
железо.

### W3. FFmpeg и внешние инструменты

Создать единый resolver, например `core/external_tools.py`:

1. сначала искать bundled `tools/ffmpeg.exe` и `tools/ffprobe.exe` рядом с
   Windows executable;
2. затем искать через `PATH`;
3. возвращать абсолютный путь;
4. формировать платформенную подсказку установки.

Перевести на resolver:

- `transcriber._convert_to_wav()`;
- `video_input.py`;
- `video_cut.py`;
- `utils.get_audio_duration()`;
- `ui/file_selector.py`;
- Live preflight.

Для source-режима FFmpeg может оставаться внешней зависимостью. Для release
installer выбрать один из двух вариантов и зафиксировать решение:

- предпочтительно: положить проверенную сборку `ffmpeg.exe`/`ffprobe.exe` в
  `tools/`, приложить license/NOTICE и проверять SHA-256 при загрузке в CI;
- допустимый первый preview: не встраивать FFmpeg, но показывать точную
  Windows-инструкцию и сохранять работу с WAV без него.

Не загружать бинарник во время обычного запуска приложения.

Тесты:

- bundled tool имеет приоритет над `PATH`;
- PATH fallback;
- корректная Windows-подсказка;
- путь к executable с пробелами;
- subprocess вызывается без `shell=True`;
- отсутствие FFmpeg не ломает WAV-сценарий.

Критерий готовности: все места используют один и тот же executable и одно
диагностическое сообщение.

### W4. Windows setup и запуск из исходников

Добавить:

- `setup-windows.ps1`;
- `run-windows.ps1`;
- `requirements-windows.lock`;
- при необходимости `requirements-build-windows.txt`.

`setup-windows.ps1` должен:

1. найти `py -3.11` или совместимый `python.exe`;
2. проверить x64;
3. создать `.venv`;
4. обновить pip/setuptools/wheel;
5. установить Windows lock;
6. выполнить import smoke для PyQt6, `sounddevice`, `python-docx` и
   `pywhispercpp`;
7. проверить FFmpeg и вывести действие, но не падать, если пользователь
   планирует работать только с WAV;
8. не требовать Administrator;
9. не менять системный PATH и ExecutionPolicy;
10. завершаться ненулевым кодом при реальной ошибке установки.

`run-windows.ps1` должен запускать только:

```powershell
.\.venv\Scripts\python.exe .\main.py
```

Скрипты должны корректно работать из каталога с пробелами.

Критерий готовности: новый пользователь выполняет две документированные
команды и получает рабочее приложение из исходников.

### W5. Процессы, отмена и frozen runtime

Разделить `main._setup_frozen_runtime()` на платформенные ветки:

- macOS сохраняет внешний whisper stack и PATH Homebrew;
- Windows использует bundled `pywhispercpp` и DLL;
- Linux сохраняет текущее поведение.

Не переносить `freeze_support()` ниже Qt-импортов.

Вынести завершение процессов в общий helper:

- `terminate()`;
- bounded `join()`;
- `kill()` при наличии;
- второй bounded `join()`;
- нейтральные сообщения без POSIX-сигналов;
- закрытие queue/pipe только в безопасном порядке.

Проверить оба процессных контура:

- offline `TranscriptionWorker`;
- `core/live/asr_worker.py`.

Добавить специальный frozen smoke mode:

```text
Whispered.exe --smoke-test
```

Он должен создать `QApplication` и `MainWindow`, проверить импорт
`pywhispercpp`, каталоги ресурсов и завершиться сам с кодом 0 без показа
диалогов и без модели.

Тесты:

- `freeze_support()` остаётся до Qt;
- Windows frozen runtime не добавляет macOS-пути;
- дочерний процесс возвращает result/error;
- cancel завершается за ограниченное время;
- повторный запуск после cancel работает;
- при spawn не появляется второе окно.

Критерий готовности: source и frozen сценарии одинаково проходят smoke и
cancel.

### W6. Live на Windows

Для MVP поддержать только microphone source.

Изменить Live UI:

- на Windows checkbox system audio disabled;
- рядом выводится локализованное сообщение «пока не поддерживается»;
- mic выбран по умолчанию;
- preflight не пытается искать Swift-helper;
- runtime не создаёт Unix socket и не импортирует macOS adapter в рабочем
  Windows-сценарии.

Сохранить macOS ScreenCaptureKit без регрессии.

Отдельный будущий этап для Windows system audio:

- провести ADR/technology spike для WASAPI loopback;
- сделать отдельный `WindowsSystemAudioSource`;
- сохранить существующий `AudioFrame` contract;
- не пытаться маскировать WASAPI под ScreenCaptureKit-helper;
- добавить защиту от эха и dual-source тесты до включения UI.

Тесты MVP:

- Windows capability matrix;
- system audio control disabled;
- preflight mic-only проходит;
- runtime mic-only не использует AF_UNIX;
- завершённая Live-сессия сохраняется в Library.

Критерий готовности: Live с микрофоном работает, неподдерживаемый источник
невозможно случайно запустить.

### W7. Windows PyInstaller package

Не переиспользовать macOS `build.py` безусловными ветками. Добавить отдельный
контур:

```text
packaging/windows/
  build-windows.ps1
  Whispered.windows.spec
  version_info.txt
  installer.iss
  README.md
```

Также:

- добавить `assets/icon.ico`, сгенерированный из исходного SVG/PNG;
- разрешить tracked Windows spec исключением в `.gitignore`;
- собирать PyInstaller `onedir`, не `onefile`;
- включить `locales/`, `prompts/`, icon, PyQt6 plugins, Qt Multimedia,
  `pywhispercpp` и его DLL;
- не включать модели;
- включить FFmpeg только согласно решению W3;
- добавить version/company/product metadata;
- создать ZIP из точного содержимого `dist/Whispered/`;
- собрать per-user installer без Administrator;
- shortcut в Start Menu обязателен, Desktop — опционален;
- uninstall не удаляет `%LOCALAPPDATA%\Whispered` без отдельного явного
  выбора пользователя.

После PyInstaller автоматически выполнить:

```powershell
.\dist\Whispered\Whispered.exe --smoke-test
```

Затем проверить состав bundle:

- нет моделей, токенов, локальных config/history/output;
- есть locale/prompt files;
- есть Qt platform plugin `qwindows`;
- есть Qt Multimedia plugins;
- есть whisper DLL;
- executable запускается без установленного Python.

Критерий готовности: ZIP распаковывается и работает в чистой VM, installer
устанавливает/запускает/удаляет приложение без admin.

### W8. CI

Расширить `.github/workflows/ci.yml` или добавить отдельный Windows workflow.

PR gate на `windows-latest`, Python 3.11:

1. checkout;
2. setup Python;
3. install Windows lock + dev dependencies;
4. `python -m compileall`;
5. `python -m ruff check .`;
6. `python -m pytest tests/ -q`;
7. импорт `pywhispercpp`;
8. PyInstaller build;
9. frozen `--smoke-test`;
10. ZIP;
11. upload unsigned artifact.

Release/nightly gate:

- короткая реальная транскрипция на CPU;
- проверка cancel;
- installer build;
- clean-VM smoke;
- подпись только из защищённого GitHub Environment;
- SHA-256 checksums;
- upload подписанных артефактов.

Не хранить PFX или пароль в репозитории. PR из fork не должен получать signing
secrets. Unsigned CI artifact должен быть явно помечен как test build.

Критерий готовности: Windows regression ловится до merge; сборка
воспроизводится одной workflow-командой.

### W9. Автотесты Windows-совместимости

Добавить или расширить:

- `tests/test_paths.py`;
- `tests/test_platform_support.py`;
- `tests/test_external_tools.py`;
- `tests/test_frozen_runtime.py`;
- `tests/test_transcription_windows.py`;
- `tests/test_live_preflight.py`;
- `tests/test_live_production_ui.py`;
- `tests/test_player_widget.py`;
- `tests/test_config.py`;
- `tests/test_exporters.py`.

Обязательные случаи:

- `%LOCALAPPDATA%` и отсутствие env;
- пробелы и кириллица в source/output/user name;
- read-only install directory;
- Unicode JSON/HTML/DOCX/PDF;
- SQLite FTS5 с кириллицей;
- микрофон отсутствует/занят/permission denied;
- LM Studio offline;
- FFmpeg отсутствует;
- модель отсутствует и download падает;
- закрытие окна во время transcription/queue/AI/Live;
- повторный запуск после crash/cancel;
- frozen resources;
- Qt Multimedia backend/plugin missing — понятная деградация без падения.

Нативные тесты с моделью не запускать на каждый PR. Вынести их в
nightly/release, кэшировать модель и публиковать лог без пользовательских
данных.

## 7. Диаризация

Диаризация не должна блокировать Windows MVP.

Порядок включения:

1. Проверить совместимые версии `torch`, `torchaudio` и `pyannote.audio` на
   Windows Python 3.11.
2. Проверить CPU и, отдельно, CUDA.
3. Проверить Hugging Face model download в `%LOCALAPPDATA%`.
4. Проверить короткий WAV с двумя спикерами.
5. Проверить отмену и закрытие приложения во время lazy load.
6. Создать отдельный optional lock/extra, чтобы базовый installer не вырос на
   несколько гигабайт.
7. В базовой сборке показывать установщик/инструкцию, а не падать при импорте.

Критерий готовности: функция либо реально работает после явно описанной
доустановки, либо честно недоступна и не мешает остальному приложению.

## 8. GPU после MVP

Не считать наличие `nvidia-smi` доказательством работоспособности CUDA backend.

Отдельный spike:

1. Собрать Windows x64 wheel `pywhispercpp` с CUDA на закреплённой версии
   Visual Studio Build Tools и CUDA Toolkit.
2. Проверить DLL dependency closure в чистой VM.
3. Сравнить Tiny/Base/Small CPU vs CUDA.
4. Проверить fallback при несовместимом драйвере.
5. Решить, поставлять ли отдельные CPU/CUDA installers.
6. Только после этого включать GPU label и переключатель в release build.

Vulkan можно исследовать отдельно как более широкий backend, но нельзя
включать без нативной матрицы Intel/AMD/NVIDIA.

## 9. Документация

После реализации обновить:

- `README.md`;
- `README.ru.md`;
- `TESTING.md`;
- `ROADMAP.md`;
- `CLAUDE.md`;
- новый `docs/WINDOWS_TESTING.md`;
- `packaging/windows/README.md`.

Документация должна содержать:

- поддерживаемую версию/архитектуру Windows;
- source setup;
- установку и обновление;
- location пользовательских данных;
- FFmpeg policy;
- LM Studio setup;
- опциональную диаризацию;
- отсутствие system-audio Live;
- CPU-only ограничение первой сборки;
- путь к log;
- известные проблемы плеера/codecs;
- инструкцию полного удаления данных.

## 10. Подпись и релиз

Preview может быть unsigned, стабильный публичный installer — нет.

Для production:

1. Получить подходящий Authenticode certificate или настроить облачную
   signing service.
2. Подписать внутренние `.exe`/`.dll`, затем installer.
3. Использовать SHA-256 digest и RFC 3161 timestamp.
4. Проверить подпись через `signtool verify /pa /v`.
5. Опубликовать SHA-256 для ZIP и installer.
6. Проверить скачанный файл, а не только workspace-копию.
7. Не выводить subject, secret identifiers и signing command с секретами в
   публичный лог.

## 11. Наблюдаемость и диагностика

Добавить в log при старте:

- Windows version и architecture;
- source/frozen;
- app version;
- user-data root без имени пользователя в отправляемой диагностике;
- путь разрешения `pywhispercpp`;
- фактический whisper backend;
- наличие FFmpeg/ffprobe;
- Qt/PyQt version;
- Qt Multimedia backend;
- LM Studio URL без ключа;
- disabled capabilities с причиной.

Добавить privacy-safe кнопку копирования диагностики. Она не должна включать:

- transcript;
- полный пользовательский путь;
- имена файлов;
- API keys / HF token;
- prompt content;
- audio;
- chat history.

## 12. Ручной release gate

Проводить на чистой Windows 11 x64 VM и хотя бы одном физическом компьютере.

### Установка

- [ ] installer работает без Python и Git;
- [ ] установка без Administrator;
- [ ] Start Menu shortcut;
- [ ] запуск после logoff/reboot;
- [ ] upgrade поверх предыдущей версии;
- [ ] uninstall;
- [ ] пользовательские данные сохранены после uninstall;
- [ ] повторная установка видит прежнюю Library.

### Пути

- [ ] пользователь с кириллицей в имени;
- [ ] source path с пробелами и кириллицей;
- [ ] output path с пробелами и кириллицей;
- [ ] приложение установлено в путь с пробелами;
- [ ] install directory read-only для обычного пользователя;
- [ ] отсутствует `LOCALAPPDATA` в изолированном тесте — fallback понятен.

### Основные функции

- [ ] WAV без FFmpeg;
- [ ] MP3/M4A/MP4 через FFmpeg;
- [ ] Tiny и Base;
- [ ] auto language и явный Russian/English;
- [ ] translate to English;
- [ ] custom vocabulary;
- [ ] cancel и повторный запуск;
- [ ] Queue 3+ файлов и cancel;
- [ ] Recorder: default и выбранный microphone;
- [ ] pause/resume Recorder;
- [ ] Library add/search/open/delete/restart;
- [ ] edit transcript и rename speakers;
- [ ] все доступные UI exports;
- [ ] PDF exporter отдельным тестом, пока его нет в меню;
- [ ] плеер MP3/WAV/MP4, seek по сегменту;
- [ ] Cut pause marking;
- [ ] EDL;
- [ ] draft MP4.

### AI

- [ ] LM Studio connection test;
- [ ] cleanup;
- [ ] пять article formats;
- [ ] Chat streaming и Stop;
- [ ] Insights;
- [ ] YouTube package через LM Studio;
- [ ] OpenAI-compatible provider с тестовым ключом;
- [ ] Anthropic с тестовым ключом;
- [ ] Book single + Markdown batch;
- [ ] LM Studio offline и restart.

### Live

- [ ] mic-only preflight;
- [ ] start/pause/resume/stop;
- [ ] incremental transcript;
- [ ] сохранение в Library;
- [ ] system audio visibly disabled;
- [ ] close during active session.

### Надёжность

- [ ] 30 минут работы без роста orphan processes;
- [ ] закрытие во время каждой фоновой операции;
- [ ] модель не загрузилась;
- [ ] FFmpeg отсутствует;
- [ ] microphone занят;
- [ ] сеть отключена;
- [ ] нет доступа на запись к выбранному output;
- [ ] log содержит причину, но не секреты.

Windows считается поддерживаемой только после прохождения всех обязательных
пунктов и регистрации известных отклонений.

## 13. Рекомендуемые границы коммитов

1. `test: capture Windows compatibility baseline`
2. `refactor: centralize platform data paths`
3. `feat: add runtime capability model`
4. `feat: resolve bundled and system ffmpeg`
5. `feat: add Windows setup and run scripts`
6. `fix: harden spawned worker cancellation on Windows`
7. `feat: expose microphone-only Live on Windows`
8. `build: add Windows PyInstaller package`
9. `ci: test and package on Windows`
10. `docs: document Windows support and release gate`

Каждый коммит должен проходить:

```bash
python -m ruff check .
python -m pytest tests/ -q
python -m compileall -q . -x ".venv|.claude|build|dist"
```

На Windows дополнительно:

```powershell
.\dist\Whispered\Whispered.exe --smoke-test
```

## 14. Финальные критерии готовности

Задача завершена, когда одновременно выполнено всё:

- Windows source setup воспроизводим;
- текущий набор тестов и новые Windows-тесты проходят;
- macOS/Linux не имеют регрессий;
- Windows CI обязателен для merge;
- frozen smoke проходит;
- clean-VM installer gate пройден;
- реальная CPU-транскрипция и cancel пройдены;
- микрофон, история, экспорт, плеер, Cut и LM Studio проверены;
- неподдерживаемые system audio/GPU невозможно принять за работающие;
- release artifact подписан и signature проверена;
- README/TESTING/ROADMAP отражают фактический статус.

## 15. Технические источники

- pywhispercpp, Windows wheels и FFmpeg prerequisites:
  https://pypi.org/project/pywhispercpp/
- PyInstaller: сборка выполняется отдельно на каждой целевой ОС:
  https://pyinstaller.org/en/stable/
- PyInstaller и обязательный `multiprocessing.freeze_support()`:
  https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html
- Qt Multimedia и платформенная проверка backend:
  https://doc.qt.io/qt-6/qtmultimedia-index.html
- Qt deployment for Windows:
  https://doc.qt.io/qt-6/windows-deployment.html
- Microsoft SignTool:
  https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool
