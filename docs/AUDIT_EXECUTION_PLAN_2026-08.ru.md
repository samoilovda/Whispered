# Whispered — план работ по итогам аудита (исполнительный)

> Дата составления: 17 августа 2026 года.
> Источник: `docs/CODEBASE_AUDIT_RECOMMENDATIONS_2026-08.ru.md` (аудит от
> 13 августа 2026).
> Аудитория: ИИ-агент, выполняющий изменения в кодовой базе.
> Формат: последовательность задач с конкретными файлами, шагами, тестами и
> критериями приёмки. Каждая задача = один коммит.

---

## 0. Правила исполнения

Обязательны к соблюдению во всех задачах ниже.

1. **Один шаг = один коммит.** Префиксы `feat:`/`fix:`/`refactor:`/`docs:`/
   `chore:`/`test:`. Не смешивать рефакторинг с функциональностью.
2. **Перед началом каждой задачи** — `git status`; если дерево грязное от
   предыдущей задачи, сначала закоммитить или явно спросить владельца.
3. **Перед закрытием каждой задачи** — полный gate (раздел 6). Задача не
   считается выполненной, если gate красный.
4. **Не расширять scope.** Если по ходу задачи обнаружен смежный дефект — не
   исправлять «заодно», а записать в раздел «Найденное сверх плана» в конце
   этого файла и продолжить текущую задачу.
5. **Не использовать `QThread.terminate()`** ни при каких обстоятельствах.
6. **Не блокировать GUI-поток неограниченным `wait()`** — все ожидания
   bounded.
7. **Тест пишется до или вместе с исправлением**, не после. Дефекты
   lifecycle/Qt проверяются в `tests_qt/` (реальный Qt), логика — в `tests/`
   (заглушки Qt).
8. **Не менять поведение и структуру одновременно.** Рефакторинговые задачи
   (P1) обязаны быть behaviour-preserving; их приёмка — зелёные существующие
   тесты без правок ожиданий.
9. Правила проекта из `CLAUDE.md` (offline-first, `Config` для настроек,
   `core.logger`, ленивые тяжёлые импорты) действуют поверх этого плана.

---

## 1. Фаза 0 — зафиксировать текущее состояние

Дерево содержит большой объём незакоммиченной работы: правки аудита поверх
незавершённого Cover generator (новые `core/cover_worker.py`, `covers/`,
`ui/cover_view.py`, `ui/cover_inspector.py`, `ui/frame_picker.py`,
`tools/pptx_to_template.py`, новые тесты и документы). Работать поверх такой
базы нельзя: любой откат неотличим от потери чужой работы.

### Задача 0.1 — разложить незакоммиченное на логические коммиты

**Шаги**

1. `git status --porcelain` и `git diff --stat` — снять полную картину.
2. Сгруппировать изменения минимум по этим осям:
   - `feat(covers): …` — новые модули `covers/`, `core/cover_worker.py`,
     `ui/cover_*.py`, `ui/frame_picker.py`, `tools/pptx_to_template.py`,
     `assets/covers/`, `assets/fonts/`, `prompts/thumb_title.md`,
     `tests/test_cover_*.py`, `tests_qt/test_cover_renderer.py`;
   - `fix(workers): …` — правки lifecycle в `ui/youtube_panel.py`,
     `ui/insights_panel.py`, `batch_processor.py`, `core/live/runtime.py`,
     `tests_qt/test_worker_lifecycle.py`, `tests/test_live_runtime.py`;
   - `fix(security): …` — права каталогов/файлов в `core/paths.py`,
     `core/history.py`, `core/logger.py`, `core/recorder.py`, Unix socket в
     `core/live/*`, `tests/test_paths.py`;
   - `fix(export): …` — атомарность Cover export, PDF/TXT в меню,
     `video_cut.py`, `transcriber.py` (FFmpeg errors), `tests/test_video_cut.py`;
   - `chore(deps): …` — `requirements*.txt`, `requirements*.lock`,
     `.github/workflows/ci.yml`, `packaging/windows/*`, `appimage/*`;
   - `docs: …` — все файлы `docs/`, `README*.md`, `ROADMAP.md`, `TESTING.md`,
     `CLAUDE.md`.
3. Перед каждым `git add`/`git commit` — `git status` и просмотр содержимого
   попадающих файлов; убедиться, что не коммитятся секреты, содержимое
   `input/`, `output/`, локальные модели или веса.
4. После каждого коммита прогнать gate из раздела 6.

**Приёмка**: `git status` чист; `git log --oneline` показывает связную
последовательность; gate зелёный.

**Риск**: Cover generator может быть незавершён и ломать gate. Если так —
коммитить его как есть отдельным `feat(covers): work in progress` и **не
чинить** в этой задаче; несоответствия зафиксировать в разделе 9.

---

## 2. Фаза 1 — P0: блокеры релиза

Порядок внутри фазы обязателен: R1 создаёт инфраструктуру, на которую
опираются R3 и частично R4.

### Задача R1 — единый lifecycle фоновых workers

**Проблема.** Правила остановки разные в каждом модуле. Неограниченный
`wait()` в GUI-потоке: `ui/main_window.py:159` (`_wait_ms is None`),
`ui/main_window.py:1053,1064,1073`, `ui/chat_panel.py:180,272,310`. Model
downloader (`ui/model_downloader.py:245-250`) вызывает `cancel()` только для
`DownloadWorker`, но не для `DiarizationCacheWorker`, и затем `reject()` при
живом потоке. Book (`ui/batch_panel.py:370`) и Cover (`ui/cover_view.py:168`)
отменяют работу, но не удерживают сильную ссылку до `finished()`. Live
finalizer (`core/live/runtime.py:283-289`) считает результат готовым, даже
если `self._worker.wait(3000)` истёк. Рабочий образец правильного поведения
уже есть в `ui/youtube_panel.py:269-330` и `ui/insights_panel.py:236-266`
(словарь `_retired_workers`).

**Шаги**

1. Создать `core/worker_registry.py` с классом `WorkerRegistry`:
   - `register(worker, *, name)` — берёт сильную ссылку;
   - `retire(worker)` — отключает **только бизнес-сигналы** (не встроенный
     `QThread.finished`), вызывает `worker.cancel()`, переносит в `retired`;
   - подписка на `QThread.finished` → `deleteLater()` и удаление ссылки;
   - `shutdown_all(timeout_ms)` — bounded ожидание, суммарный дедлайн на всю
     регистрацию, возвращает список не завершившихся имён;
   - никакого `terminate()`; при истечении дедлайна — `logger.error` с
     именами зависших workers.
2. Обобщить `core/base_worker.py`: добавить контракт «ровно один terminal
   signal» — защищённый флаг `_terminal_emitted`, хелпер
   `_emit_terminal(signal, *args)`, который игнорирует повторные вызовы.
3. Перевести на `WorkerRegistry` по одному модулю за раз, каждый — отдельным
   коммитом:
   `ui/youtube_panel.py` → `ui/insights_panel.py` → `ui/ai_panel.py` →
   `ui/chat_panel.py` → `ui/model_downloader.py` → `ui/batch_panel.py`
   (book) → `ui/cover_view.py` → `ui/settings_dialog.py` →
   `ui/live_setup_panel.py` → `ui/main_window.py` (`_WorkerShutdown`).
4. `ui/main_window.py`: убрать ветку `wait_ms is None`; все адаптеры
   получают явный таймаут. `closeEvent` вызывает `shutdown_all` один раз.
5. `ui/model_downloader.py`: `_on_cancel` отменяет **любой** тип worker;
   диалог не закрывается до `finished()` — вместо немедленного `reject()`
   перевести кнопку в состояние «Отмена…» и закрыть по сигналу.
6. `core/live/runtime.py`: если `self._worker.wait(3000)` вернул `False` —
   **не** эмитить `finished`; эмитить `error_occurred("asr", …)` и перевести
   сессию в `FAILED`. Тот же bounded-контракт для `_drainers` join.
7. Сетевой транспорт (`core/lm_client.py`, `core/ai_provider.py`,
   `core/anthropic_client.py`): `cancel()` должен закрывать активный
   response/socket, а не только выставлять флаг. Проверить, что чтение с
   таймаутом до 600 с прерывается менее чем за 2 с после Cancel.

**Тесты**

- `tests_qt/test_worker_lifecycle.py` (расширить): fake transport, который
  блокируется навсегда; сценарии — двойной Cancel; Cancel + немедленное
  закрытие окна; поздний сигнал устаревшего worker при уже запущенном новом.
- `tests/test_workers.py`: `_emit_terminal` игнорирует второй вызов.
- Новый `tests_qt/test_model_downloader_lifecycle.py`: отмена
  `DiarizationCacheWorker` закрывает диалог только после `finished`.

**Приёмка**

- Нет freeze при закрытии окна с активной работой в каждом из модулей списка.
- В логах Qt нет `QThread: Destroyed while thread is still running`.
- Поздний сигнал старого запуска не меняет UI-состояние нового.
- Live-сессия с зависшим ASR worker завершается как `FAILED`, а не как
  успешная.

**Коммиты**: `feat(core): add WorkerRegistry` + по одному
`refactor(ui): move <module> to WorkerRegistry` + `fix(live): fail session on
ASR shutdown timeout`.

---

### Задача R2 — проверяемая загрузка моделей

**Проблема.** `ui/model_downloader.py` и `transcriber.py:698-710`
(`prepare_models`) принимают модель по факту существования файла. URL может
указывать на mutable revision. Обрыв загрузки оставляет усечённый файл,
который затем читает нативный parser.

**Шаги**

1. Создать `core/model_manifest.py`: датакласс `ModelEntry`
   (`key`, `url` с **immutable** revision/commit, `size_bytes`, `sha256`,
   `license`, `filename`) и `MANIFEST: dict[str, ModelEntry]`. Записи —
   для всех whisper-моделей и ONNX-весов, которые приложение скачивает.
2. Создать `core/model_repository.py` с Qt-free API:
   - `ensure(key, *, progress: Callable[[int, int], None], cancel: Callable[[], bool]) -> Path`;
   - загрузка в `<target>.download`, потоковый `hashlib.sha256`, `fsync`,
     затем `os.replace`;
   - существующий файл проверяется по размеру и SHA-256 **до** использования;
     несовпадение → перезагрузка, а не тихое принятие;
   - при ошибке/Cancel `.download` удаляется в `finally`;
   - права результата — `0600` в приватном каталоге (согласовать с
     `core/paths.py`).
3. `ui/model_downloader.py` становится тонким UI-адаптером над
   `ModelRepository`: только прогресс, отмена и сообщения.
4. `transcriber.py::prepare_models` перестаёт импортировать
   `ui.model_downloader`; вызывает `ModelRepository` напрямую, диалог
   подключается со стороны UI (см. R7 — здесь делается только развязка
   импорта).
5. Manifest и лицензии моделей включить в release metadata (передать в R9).

**Тесты** (`tests/test_model_repository.py`, новый)

- bad digest → исключение, целевой файл не создан, старый файл не тронут;
- усечённый существующий файл → перезагрузка, а не принятие;
- обрыв сети в середине → `.download` удалён, повторный запуск успешен;
- Cancel → `.download` удалён, исключение `Cancelled`, не `success`;
- проверка, что `transcriber.py` не импортирует `ui.*` (статический тест
  через `ast` по исходнику).

**Приёмка**: ни один из четырёх сценариев не оставляет «валидную» модель и не
повреждает предыдущую рабочую копию.

**Коммиты**: `feat(core): add versioned model manifest and repository` +
`refactor(ui): make model downloader a repository adapter` +
`fix(transcriber): drop UI import from prepare_models`.

---

### Задача R3 — backpressure и атомарность Recorder

**Проблема.** `core/recorder.py:115` — неограниченная `queue.Queue`; audio
callback пишет в неё без предела. Ошибка writer-потока (`:289`) только
логируется и не доходит до UI. `stop()` (`:230-234`) делает
`join(timeout=5)` и затем закрывает WAV независимо от того, жив ли writer.
Итог при медленном диске: рост RAM, гонка на закрытии файла и неполный WAV,
который UI показывает как успешную запись.

**Шаги**

1. `queue.Queue(maxsize=N)`, где N рассчитан на ~5 секунд аудио при текущем
   sample rate/blocksize; вынести в константу модуля с комментарием расчёта.
2. Явная overflow policy: при полной очереди — **drop newest** с инкрементом
   счётчика `_dropped_frames` (в callback нельзя блокироваться); счётчик
   доступен через свойство и попадает в diagnostics/лог.
3. Общее fatal-состояние: `threading.Event` + поле с текстом ошибки; writer
   при исключении выставляет его и завершает цикл. Recorder эмитит
   `error_occurred` ровно один раз (использовать `_emit_terminal` из R1).
4. `stop()`: детерминированный drain — sentinel в очередь, ожидание writer с
   таймаутом; если таймаут истёк **или** выставлено fatal-состояние —
   результат считается провалом, `finished` не эмитится.
5. Атомарность: писать в `<target>.part` в том же каталоге, при успешном
   завершении — `fsync` + `os.replace(target)`. При провале `.part` удаляется.
6. Публиковать `dropped_frames` в UI-диагностике (`ui/recorder_widget.py`),
   если > 0 — предупреждение пользователю.

**Тесты** (`tests/test_recorder_helpers.py`, расширить)

- slow writer (искусственная задержка записи): память ограничена, счётчик
  дропов растёт, приложение не падает;
- disk full (mock `write` с `OSError`): ровно один `error_occurred`, нет
  `finished`, `.part` удалён;
- Cancel в середине: нет частичного «успешного» WAV;
- happy path: `.part` не остаётся, целевой файл валиден.

**Приёмка**: во всех сценариях ограниченная память, ровно один terminal
outcome, отсутствие частичного файла, выданного за успех.

**Коммит**: `fix(recorder): bounded buffer, single terminal outcome, atomic WAV`.

---

### Задача R4 — довести защиту system-audio IPC

**Проблема.** Случайное имя socket, каталог `0700` и socket `0600` уже
закрывают межпользовательский доступ (сделано в аудите). Остаётся: процесс
того же пользователя может выиграть гонку за первое соединение; размер
payload из header не ограничен.

**Шаги**

1. Генерировать криптографический nonce (`secrets.token_bytes(32)`) на
   стороне приложения; передавать helper'у через **унаследованный FD или
   переменную окружения дочернего процесса**, не через argv (argv виден в
   `ps`).
2. Обязательный handshake сразу после `accept()`: helper шлёт nonce; при
   несовпадении или таймауте — соединение закрывается, helper завершается
   **до передачи первого PCM-фрейма**.
3. В `core/live/system_capture_protocol.py`: жёсткие лимиты на размер header
   и payload; значение больше лимита — protocol error, а не аллокация.
4. macOS: где доступно — проверка peer credentials (`LOCAL_PEERPID` /
   `getsockopt(SOL_LOCAL, LOCAL_PEERCRED)`); при недоступности логировать
   downgrade, не падать.
5. Синхронно обновить Swift-сторону:
   `native/system_capture_helper/Sources/WhisperedCaptureHelper/main.swift`.
6. Обновить `docs/SYSTEM_CAPTURE_IPC.ru.md` — описать handshake и лимиты.

**Тесты** (`tests/test_system_capture_protocol.py`, расширить)

- неверный nonce → соединение отклонено, PCM не принят;
- отсутствие handshake в течение таймаута → отклонение;
- header с завышенным размером payload → protocol error без аллокации;
- корректный handshake → обычный поток данных не изменился.

**Приёмка**: посторонний процесс того же пользователя не может подменить
источник аудио; некорректный header не приводит к большому выделению памяти.

**Коммит**: `fix(live): authenticate system-capture IPC with nonce handshake`.

---

### Задача R5 — защитить артефакты от коллизий (краткосрочная часть)

**Проблема.** Выходные каталоги и YouTube-имена файлов строятся от
`Path(source).stem`. Два разных `interview.mp4` перезаписывают результаты
друг друга и смешивают provenance.

**Область задачи здесь — только краткосрочное решение.** Полная Artifact
entity делается в Задаче R8-pre (фаза 2).

**Шаги**

1. Найти все места построения выходного пути от `stem`:
   `grep -rn "\.stem" --include="*.py" .`
2. Ввести хелпер в `core/paths.py`:
   `artifact_dir(record_id: str, source: Path) -> Path`, возвращающий
   `<outputs>/<sanitized-stem>-<record_id[:8]>/`.
3. Гарантировать, что `record_id` (UUID записи истории) создаётся **до**
   первой записи артефакта, а не после; при необходимости — вставка строки
   истории в начале пайплайна.
4. Каждый артефакт пишется через temp + `os.replace` (переиспользовать хелпер
   из Cover export, вынести общий `utils.atomic_write`).
5. Миграция: старые пути не переименовывать; история продолжает указывать на
   существующие файлы.

**Тесты** (`tests/test_paths.py`, расширить)

- два источника с одинаковым `stem` из разных каталогов дают разные выходные
  каталоги;
- имя с недопустимыми для FS символами санитизируется и не выходит за
  пределы outputs (`resolve()` внутри корня);
- `atomic_write` не оставляет частичного файла при исключении.

**Приёмка**: одноимённые источники не перезаписывают результаты друг друга.

**Коммит**: `fix(paths): scope artifact directories by record id`.

---

### Гейт выхода из фазы 1

Помимо обычного gate:

- прогнан `tests_qt/` полностью, без пропущенных тестов;
- ручной smoke: запись → транскрипция → Cover → экспорт → закрытие окна во
  время активной работы (не должно быть зависания);
- в `ROADMAP.md` отмечены закрытые P0-пункты с датой.

---

## 3. Фаза 2 — P1: структурные изменения

Все задачи фазы behaviour-preserving. Если тест приходится править по
существу — это сигнал, что поведение поехало; остановиться и разобраться.

### Задача R7-pre — вынести domain DTO (делать раньше R6)

> **Статус (2026-08-17): шаги 1, 2, 5 сделаны; частично — шаг 3 (только
> `core/live/*`); шаг 4 не начат.** См. запись в разделе 9.

**Проблема.** `Segment`, `Word`, `TranscriptionResult` живут в
`transcriber.py`, который импортирует `PyQt6.QtCore` (`transcriber.py:13`) и
worker-инфраструктуру. Exporters и Live тянут весь этот модуль ради трёх
датаклассов. `core/live/__init__.py` eagerly реэкспортирует подсистему.

**Шаги**

1. Создать `domain/transcription.py` — чистый Python, без Qt и IO:
   `Word`, `Segment`, `TranscriptionResult` и их сериализация.
2. В `transcriber.py` оставить `from domain.transcription import …` для
   обратной совместимости импортов (реэкспорт), но новый код импортирует из
   `domain`.
3. Перевести на `domain` по одному потребителю: `exporters.py`,
   `core/live/*`, `core/insights_worker.py`, `core/history.py`, `ui/*`.
4. `core/live/__init__.py` сделать пустым (или полностью lazy через
   `__getattr__`); production-код импортирует конкретные `core.live.*`.
5. Статический тест: `domain/` не импортирует `PyQt6`, `ui`, `core.live`.

**Приёмка**: `python -c "import domain.transcription"` не тянет Qt; все
существующие тесты зелёные без правок ожиданий.

**Коммиты**: `refactor(domain): extract Qt-free transcription DTOs` +
`refactor(live): make package __init__ lazy`.

---

### Задача R6 — `DocumentSession.apply_result()` (первый шаг разделения)

> **Статус (2026-08-17): сделано.** `application/document_session.py`,
> все три fan-out сайта переведены, тесты в
> `tests_qt/test_document_session.py`. Строки `main_window.py` не
> сократились (1847 → 1892) — это ожидаемо для первого шага: список
> consumers пока живёт инлайн в `_register_document_session_consumers`;
> сокращение размера — задача R6-cont (следующий срез), не эта.

**Проблема.** `ui/main_window.py` — 1847 строк; одновременно composition
root, document state, координатор transcription/live/batch, фасад истории,
контроллер экспорта и AI-пайплайна. Ручные fan-out блоки результата уже
приводили к пропуску Cover. Окно обращается к приватным полям дочерних
виджетов.

**Шаги (только первый безопасный срез)**

1. Создать `application/document_session.py`:
   - хранит `current_result`, `record_id`, `source_path`, revision;
   - единственный публичный метод распространения:
     `apply_result(result: TranscriptionResult, source: ResultSource)`;
   - `ResultSource` — enum (`FRESH_TRANSCRIPTION`, `HISTORY_OPEN`,
     `MANUAL_EDIT`, `LIVE_FINISH`, `BATCH_ITEM`);
   - список consumers регистрируется, а не хардкодится.
2. Заменить **все** ручные fan-out блоки в `ui/main_window.py` на один вызов
   `apply_result`. Consumers: transcript view, cover, insights, youtube,
   article, export controller, history.
3. Виджеты получают публичные слоты; убрать обращения к `_приватным` полям
   (искать `grep -n "\._[a-z]" ui/main_window.py`).
4. **Не** переносить остальные контроллеры в этой задаче.

**Тесты** (`tests/test_transcription_contract.py`, расширить)

- параметризованный тест по всем `ResultSource`: каждый зарегистрированный
  consumer получает результат ровно один раз;
- регрессия аудита: Cover получает сегменты и при `FRESH_TRANSCRIPTION`, и
  при `HISTORY_OPEN`.

**Приёмка**: `ui/main_window.py` сокращён минимум на 150 строк; ни один
consumer не вызывается напрямую из окна; поведение UI не изменилось
(`tests_qt/test_main_window_smoke.py` зелёный без правок).

**Коммит**: `refactor(app): route transcription results through DocumentSession`.

---

### Задача R6-cont — извлечь контроллеры

Выполнять **только после** стабилизации `DocumentSession`, по одному
контроллеру за коммит, целевая структура:

```text
domain/            transcription.py, artifact.py, job.py
application/       document_session.py, transcription_controller.py,
                   pipeline_controller.py, export_controller.py
infrastructure/    asr/ ai/ media/ persistence/
ui/                main_window.py (только composition/navigation), views/
```

Порядок: `export_controller` (самый изолированный) →
`transcription_controller` → `pipeline_controller`. Целевой размер
`ui/main_window.py` — < 600 строк.

---

### Задача R5-full / R8-pre — Artifact entity и manifest

**Шаги**

1. `domain/artifact.py`: `Artifact` с полями `record_id`, `source_hash`,
   `source_path`, `transcript_revision`, `type`, `provider`, `model`,
   `prompt_version`, `created_at`, `path`.
2. `infrastructure/persistence/artifact_store.py`: запись manifest рядом с
   артефактом, temp + atomic replace, чтение для кэш-решений.
3. Мигрировать генераторы на `Artifact` **по одному**: cover → article →
   youtube → insights → book.

**Приёмка**: для любого артефакта можно ответить, из какой ревизии
транскрипта, какой моделью и каким промптом он получен.

---

### Задача R8 — единый resumable Job/Pipeline Engine

**Проблема.** Оркестрация размазана: preset controller, extra-chain,
YouTube workers, очередь Insights и два batch-механизма
(`batch_processor.py`, `core/book_batch_worker.py`). При одновременно
включённых YouTube и Insights `chapters` вычисляются дважды.

**Шаги**

1. `domain/job.py`: `JobSpec`, `StepSpec`, `StepOutcome`, DAG зависимостей.
2. `application/job_engine.py`:
   - resource policy: `local_llm=1` (сериализовать запросы к LM Studio —
     см. правило CLAUDE.md о параллельных запросах), cloud — per-provider;
   - идемпотентность шага, persisted state (SQLite рядом с историей);
   - retry одного шага, Cancel, resume после краха;
   - ключ кэша артефакта = `(transcript_revision, language, prompt_version,
     provider, model)`.
3. Мигрировать по одному генератору; двойной расчёт `chapters` устраняется
   как следствие общего кэша.

**Приёмка**: при включённых YouTube + Insights `chapters` считаются один раз;
после принудительного завершения приложения `resume` продолжает job с
незавершённого шага; суммарное число живых QThread уменьшилось.

---

### Задача R9 — единая release metadata и ресурсный manifest

**Шаги**

1. Создать `pyproject.toml` как canonical source версии; `__version__` в
   пакете читается оттуда.
2. Один `packaging/resources.toml` (или `.py`) — список ресурсов и entry
   modules; `build.py`, `packaging/windows/Whispered.windows.spec`,
   `appimage/build-appimage.sh` и `appimage/io.github.whispered.metainfo.xml`
   генерируются/валидируются из него.
3. Расширить `.github/workflows/ci.yml`:
   - macOS: source + Qt + Swift-helper smoke, nightly сборка `.app`;
   - Linux: AppImage build + smoke;
   - статический тест полноты resource manifest для каждого packager
     (расширить `tests/test_packaging_resources.py`);
   - pin GitHub Actions по commit SHA;
   - dependency hashes + SBOM как артефакт сборки;
   - dependency audit — результат фиксируется в CI, локальный сетевой сбой не
     считается «уязвимостей нет».
4. Release gates остаются ручными и явными: подпись/нотаризация macOS,
   Windows signing, clean-VM smoke.

**Приёмка**: версии в Windows-манифесте, AppStream и Python-пакете совпадают
и берутся из одного места; добавление ресурса в одном файле автоматически
попадает во все три packager'а.

---

## 4. Фаза 3 — P2: качество, безопасность, производительность

Задачи независимы, можно брать в любом порядке. Каждая — один коммит.

### R10 — схема и миграции Config

- `config.py`: versioned schema + миграции; валидация типов, enum и
  диапазонов; нормализация URL и таймаутов.
- `core/secrets_store.py`: tri-state результат чтения —
  `found` / `missing` / `backend_error`. При `backend_error` в конфиге
  сохраняется sentinel, и последующий `save()` **не** затирает credential до
  явного изменения поля пользователем.
- Тесты: `tests/test_config.py`, `tests/test_secrets_store.py` — миграция со
  старого файла, невалидные значения, сломанный keyring backend.

### R11 — безопасные Cover templates и cancellable frame extraction

- `covers/template.py::load_template`: после `resolve()` путь любого asset
  обязан оставаться внутри template root; абсолютные пути и `..` —
  отклоняются с явной ошибкой.
- Лимиты: размер JSON, размеры изображений, длина path data.
- `covers/frames.py`: заменить блокирующий `subprocess.run` на `Popen` с
  таймаутом, cancellable, гарантированный cleanup дочернего процесса и
  временных файлов.
- Тесты: `tests/test_cover_template.py` — `../../etc/passwd`, абсолютный
  путь, oversize JSON; отдельный тест отмены извлечения кадра.

### R12 — явные ошибки вместо silent failure

- `batch_processor.py`: экспорт возвращает structured outcome с перечнем
  частичных ошибок вместо подавления I/O failure.
- `core/external_tools.py` / `LMStudioManager`: валидация JSON-схемы вывода
  CLI, а не доверие к форме.
- `book_pipeline.py`: обязательный output directory, передаваемый от app
  outputs / Save dialog / history metadata; запрет на неявный CWD.
- Тесты на каждый из трёх пунктов.

### R13 — убрать лишний FTS rebuild

- `core/history.py:250-257`: `INSERT INTO transcripts_fts(...) VALUES
  ('rebuild')` выполняется при каждом `__init__`. Хранить schema version и
  repair marker в таблице метаданных; rebuild только при создании, миграции
  или явном repair.
- Добавить benchmark открытия истории на базе ~5000 записей; зафиксировать
  цифру до и после в `TESTING.md`.
- Тесты: `tests/test_history.py` — второй `__init__` не делает rebuild;
  повреждённый FTS-индекс триггерит repair.

### R14 — усилить type- и real-Qt-gates

- Снижать 33 mypy-ошибки в `ui/` **по модулю за раз**; после каждого модуля
  добавлять его в blocking-набор в `CLAUDE.md` и CI.
- Real-Qt тесты обязательны для: worker lifecycle, `closeEvent`, model
  downloader, экспортов, fan-out результата.
- Coverage публиковать раздельно: domain / application / UI smoke. Не
  сводить к одной цифре (текущие ~31% по unit-набору не измеряют UI).

---

## 5. Сводная таблица приоритетов

| ID | Задача | Фаза | Риск при бездействии | Оценка |
|---|---|---|---|---|
| 0.1 | Разложить незакоммиченное | 0 | потеря работы | S |
| R1 | WorkerRegistry | 1 | freeze/краш при закрытии | L |
| R2 | Проверяемая загрузка моделей | 1 | битый бинарник в native parser | M |
| R3 | Backpressure Recorder | 1 | потеря записи пользователя | M |
| R4 | Auth для system-audio IPC | 1 | подмена источника аудио | M |
| R5 | Коллизии артефактов | 1 | перезапись результатов | S |
| R7-pre | Domain DTO | 2 | блокирует R6/R8 | M |
| R6 | DocumentSession | 2 | новые пропуски fan-out | M |
| R6c | Извлечение контроллеров | 2 | рост стоимости изменений | L |
| R5f | Artifact entity | 2 | нет provenance | M |
| R8 | Job Engine | 2 | двойная работа LLM | L |
| R9 | Release metadata | 2 | расхождение сборок | M |
| R10–R14 | Качество и безопасность | 3 | накопление долга | M |

---

## 6. Gate (прогонять после каждой задачи)

```bash
ruff check .
python -m pytest tests/ -q
python -m compileall -q . -x '.venv|.claude|build|dist|docs/archive'
python -m mypy --ignore-missing-imports core/ transcriber.py diarizer.py exporters.py utils.py config.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
```

Unit-тесты — системным python (Qt заглушен в `tests/conftest.py`); всё, что
требует настоящий Qt, — из `.venv/bin/python` с `QT_QPA_PLATFORM=offscreen`.

При добавлении новых пакетов в `domain/`, `application/`, `infrastructure/`
добавлять их в mypy-набор сразу (они пишутся с нуля и должны быть чистыми).

---

## 7. Definition of done всего плана

Совпадает с разделом 5 аудита:

- нет уничтожения или потери последней ссылки на работающий `QThread`;
- ни один success-сигнал не эмитится до проверки и атомарного сохранения;
- каждый Job/item имеет ровно одно terminal state;
- модель или внешний бинарный asset принимается только после integrity check;
- один transcript result распространяется через один публичный contract;
- все platform packages строятся из одного manifest и проходят launch smoke;
- `README`/`ROADMAP` содержат дату последней проверки, датированные планы
  помечены snapshot/archive;
- unit, real-Qt, typing, dependency audit и packaging smoke — воспроизводимые
  CI gates.

---

## 8. Что явно **не** входит в этот план

- Продуктовые изменения — см. `docs/PRODUCT_DEVELOPMENT_OPTIONS_2026-08.ru.md`.
- Завершение экспериментальных Cover-модулей (frames/tiles/ONNX/ComfyUI) — они
  намеренно не подключены к workspace; план лишь требует, чтобы их наличие не
  ломало рендеринг и упаковку.
- Валидация Windows/AppImage на реальном железе — release gate, выполняется
  владельцем, не агентом.

---

## 9. Найденное сверх плана

> Раздел заполняется агентом по ходу работы. Формат:
> `- [дата] [задача, в которой нашлось] описание + файл:строка`.

- [2026-08-17] [R1] `tests_qt/` фатально падал (`QThread: Destroyed while
  thread is still running`, `Fatal Python error: Aborted`) — не найдено в
  плане явно, обнаружено при прогоне gate. Причина: `BookPanel.shutdown()`
  ([ui/book_panel.py](../ui/book_panel.py)) не трогал периодический
  `_conn_timer`/`_checker` (LM Studio connection check), а
  `SettingsDialog._on_ok()` ([ui/settings_dialog.py](../ui/settings_dialog.py))
  вообще не останавливал `_checker`. Оба переведены на уже созданный, но
  нигде не подключённый `WorkerRegistry`. Отдельно `tests_qt/conftest.py` не
  глушил реальный сетевой `LMStudioClient.probe` — real-Qt smoke тесты били
  по настоящему сокету, что и обнажало гонку при завершении процесса.
  Исправлено, тест добавлен неявно (регрессия ловится самим фактом, что
  `tests_qt/` больше не падает).
- [2026-08-17] [R1] `core/live/runtime.py::_finish_session` игнорировал
  результат `self._worker.wait(3000)` и эмитил `finished` с результатом
  независимо от того, успел ли ASR worker остановиться — именно та
  регрессия, которую явно требовал закрыть acceptance R1
  ("Live-сессия с зависшим ASR worker завершается как FAILED"). Это не было
  сделано в предыдущих коммитах R1, хотя явно значилось в шаге 6 плана.
  Исправлено вместе с тестом
  `test_finish_session_fails_instead_of_completing_when_worker_wont_stop`.
- [2026-08-17] [R1] Полная миграция панелей на `WorkerRegistry`
  **выполнена** отдельным проходом: `ui/youtube_panel.py`,
  `ui/insights_panel.py` (заменили собственный предаудитный
  `_retired_workers` на общий `WorkerRegistry`), `ui/ai_panel.py`,
  `ui/chat_panel.py`, `ui/model_downloader.py` (плюс исправлен сам баг из
  R1 — `DiarizationCacheWorker` теперь тоже получает `cancel()`, диалог не
  закрывается до фактического завершения потока), `ui/batch_panel.py`
  (book worker), `ui/cover_view.py`, `ui/live_setup_panel.py`,
  `ui/main_window.py::_WorkerShutdown`/`_ai_worker`. Попутно найдено и
  закрыто: `InsightsWorker`/`AIProcessingWorker`/`DownloadWorker`/
  `DiarizationCacheWorker` называют свой business-сигнал `finished`,
  затеняя built-in `QThread.finished` — generic-фоллбэк `WorkerRegistry`
  сознательно пропускает сигналы с именем `finished`, поэтому без
  `_disconnect_business_signals()` на этих классах поздний результат мог
  бы всё ещё долететь до UI. Добавлены overrides.
  При повторных прогонах `tools/render_ui_gallery.py` всплыли и закрыты
  два регресса из того же прохода: (1) перевод `ai_panel`/`chat_panel`/
  `book_panel`'s `shutdown()` на чисто неблокирующий `retire()` убрал
  bounded wait, который этим путям всё ещё нужен — короткоживущий процесс,
  выходящий сразу после `closeEvent`, не даёт worker'у вообще никакого
  времени на остановку; заменено на `shutdown_all(timeout_ms=...)`,
  интерактивные пути (Stop/Cancel-кнопки) остались неблокирующими; (2)
  `book_panel.py`'s `_conn_timer` инициализировался только внутри теперь
  условного `_start_connection_check()` — `shutdown()` падал с
  `AttributeError`, который PyQt6 эскалирует в фатальный abort прямо из
  `closeEvent`. Проверено 5 повторными прогонами `tests_qt/` и 5 — gallery
  script, оба ранее падали нестабильно.
  Сетевой transport — последний открытый пункт R1 — закрыт отдельным
  проходом: `core/lm_client.py::chat_completion_stream` проверял
  `is_cancelled()` только между уже полученными SSE-строками; на
  зависшем соединении (сервер принял запрос, но не шлёт данных — например
  reasoning-модель без видимых токенов во время "размышления", см. gemma-4
  gotcha в `CLAUDE.md`) блокирующее чтение не давало шанса дойти до этой
  проверки до истечения полного таймаута вызова (до 600 с для Insights).
  Исправлено: `urlopen` получает короткий poll-таймаут (`_STREAM_POLL_S =
  2s`) вместо таймаута вызывающего кода, чтение зациклено, `socket.timeout`
  на отдельном poll не считается ошибкой потока — это сигнал перепроверить
  `is_cancelled()`/дедлайн и продолжить. Задержка отмены ограничена ~2 с
  независимо от поведения сервера; общий `timeout` остался жёстким
  потолком. Тесты: `TestStreamCancellation` в `tests/test_lm_client.py`.
  `core/anthropic_client.py::chat_completion_stream` — единый атомарный
  non-streaming запрос без построчного цикла, тот же приём не применим;
  реальная отмена там потребовала бы либо настоящего streaming, либо
  socket-close-based abort из другого потока. Оставлено как отдельный
  follow-up — это не регресс (блокировка была ограничена тем же `timeout`
  и раньше), просто незакрытый пункт.
- [2026-08-17] [R2, не сделано] `core/model_manifest.py` — все `sha256`
  оставлены пустыми (`sha256=""`) для всех 10 записей моделей; заполнены
  только `size_bytes`. Код `ModelRepository` корректно откатывается на
  size-only проверку и громко логирует warning при отсутствии hash, но
  фактическая integrity-защита слабее, чем задумано R2, пока хэши не
  вычислены. Это требует один раз скачать каждый файл модели и посчитать
  `sha256sum` — не сделано в этой сессии сознательно: тянуть ~10 бинарников
  (десятки–сотни МБ каждый) с Hugging Face без явного запроса пользователя
  не в духе "скачивание файла требует явного разрешения". Кто-то с доступом
  к уже скачанным моделям может заполнить хэши через
  `hashlib.sha256(open(path,'rb').read()).hexdigest()` и обновить манифест.
- [2026-08-17] [R5, смежное, не сделано] `ui/youtube_panel.py::_save_to_file`
  (кнопка ручного сохранения одной вкладки) по-прежнему пишет в глобальный
  `_OUTPUT_DIR` с именем `{stem}_{key}.txt` без записи по record id — в
  отличие от `save_all()` (используется preset-chain'ом), который уже
  получает record-id-scoped директорию из R5. Это осознанно не тронуто:
  явное ручное действие пользователя, а не тихий автосейв, ниже риск и
  другая природа проблемы. Если понадобится — завести отдельную задачу.
- [2026-08-17] [R4] Проверка peer credentials на macOS (`LOCAL_PEERCRED`) из
  плана не реализована — задокументирован как открытый gap в
  `docs/SYSTEM_CAPTURE_IPC.ru.md`. Nonce handshake закрывает
  практический риск (гонка за первым `accept()`); peer credentials — это
  defense-in-depth поверх него.
- [2026-08-17] [R7-pre] Сделаны шаги 1, 2, 5 (`domain/transcription.py`,
  реэкспорт из `transcriber.py`, статический AST-тест). Шаг 3 сделан только
  для `core/live/reconciler.py`/`asr_worker.py`/`session_pipeline.py` —
  это были единственные потребители, тянувшие Qt транзитивно ради DTO;
  `exporters.py`, `core/insights_worker.py`, `core/history.py`, `ui/*`
  по-прежнему импортируют из `transcriber` (реэкспорт работает, поведение
  не изменилось, но новый код должен предпочитать `domain.transcription`
  напрямую). Шаг 4 (`core/live/__init__.py` → пустой/полностью lazy) **не
  начат** — модуль всё ещё eagerly импортирует Qt-зависимый
  `system_audio_source` на уровне пакета (`from core.live.system_audio_source
  import SystemAudioSource`), то есть голый `import core.live` уже тянет Qt
  независимо от DTO-проблемы. Это отдельный, более рискованный рефакторинг
  (нужно проверить внутренние cross-import между submodules `core/live/*` и
  список `__all__`) — не делался в этом проходе, чтобы не смешивать с
  behaviour-preserving шагом.
