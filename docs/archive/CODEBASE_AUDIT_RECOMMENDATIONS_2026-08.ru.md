# Whispered: аудит кодовой базы и рекомендации

> Дата: 13 августа 2026 года.  
> Scope: production Python/Qt-код, unit и real-Qt тесты, зависимости,
> shell/PowerShell setup, macOS/Windows/AppImage packaging, документация и
> локальная privacy/security-модель.  
> Это технический backlog. Варианты развития продукта вынесены отдельно в
> `PRODUCT_DEVELOPMENT_OPTIONS_2026-08.ru.md`.

## 1. Итог аудита

Кодовая база в целом работоспособна: unit- и offscreen-Qt-наборы проходят,
Ruff чист, блокирующий mypy-набор для `core/` и движков чист. При этом аудит
нашёл несколько реальных lifecycle- и data-integrity-дефектов, которые не
видны в unit-тестах с заглушками Qt, а также заметный дрейф между кодом,
упаковкой и документацией.

Наиболее важные исправления уже внесены:

- fatal exception и Cancel в batch теперь всегда завершаются ровно одним
  terminal signal и переводят каждый item в конечное состояние;
- Live ASR worker отменяется, если ни один источник не смог стартовать;
- YouTube/Insights больше не вызывают `deleteLater()` для работающего
  `QThread`: просроченные workers удерживаются до фактического `finished()`;
- Cover получает сегменты после свежей транскрипции и открытия истории, а не
  только после ручного редактирования текста;
- PDF и TXT с таймкодами выведены в меню экспорта;
- приватные каталоги приложения ограничиваются режимом `0700`, а история,
  WAV и log — `0600`; initial prompt больше не попадает в лог;
- Unix socket Live получает случайное имя, живёт в приватном каталоге и
  после bind ограничивается режимом `0600`;
- Cover export проверяет результат кодирования, сначала готовит весь набор во
  временных файлах и не сообщает успех при частичной записи;
- FFmpeg conversion отличает отсутствие FFmpeg от повреждённого медиа,
  timeout и другой ошибки конвертации;
- draft video создаётся во временном файле рядом с целью и заменяет итог
  атомарно; apostrophe в ffconcat-path экранируется;
- runtime/dev/Windows lock-файлы синхронизированы с `onnxruntime`, mypy и
  `types-requests`; поддерживаемый runtime честно зафиксирован как CPython
  3.11;
- Windows и AppImage manifests дополнены Cover-ресурсами и текущими
  production-модулями; macOS setup больше не скачивает невидимую приложению
  модель в каталог репозитория;
- README и активные планы отделяют доступный Cover MVP от неподключённых
  frames/tiles/ONNX/ComfyUI и корректно описывают keyring, checklist и
  support matrix.

Аудит начат поверх незакоммиченной работы владельца над Cover generator.
Эти изменения сохранены; исправления делались поверх них без сброса дерева.

## 2. Проверки и ограничения проверки

Финальный gate должен включать:

```bash
ruff check .
python -m compileall -q -x '__pycache__|\.git|venv|\.venv|build|dist|docs/archive' .
python -m pytest tests/ -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
mypy --ignore-missing-imports core/ transcriber.py diarizer.py \
  exporters.py utils.py config.py
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
```

Обычное покрытие unit-набора остаётся около 31%: это не оценка качества UI,
поскольку `tests/` намеренно подменяет PyQt6 заглушками, а `tests_qt/`
запускается отдельно. Полный mypy по `ui/` пока информационный и на момент
аудита показывал 33 ошибки в 10 файлах. Это контролируемый долг, но обе цифры
нужно отслеживать как trend, а не использовать только общий счётчик тестов.

Сборка Windows и AppImage на текущем macOS-хосте не выполнялась. Исправлены
их статические manifests и добавлены regression contracts, но Windows
hardware/clean-VM/signing и Linux AppImage smoke остаются release gates.
Проверка уязвимостей пакетов зависит от сетевой базы advisories; её результат
следует фиксировать в CI вместе с SBOM, а не считать локальный сетевой сбой
признаком отсутствия уязвимостей.

## 3. Приоритетные рекомендации

### P0 — до следующего публичного релиза

#### R1. Единый lifecycle для всех фоновых workers

YouTube и Insights исправлены, но правила завершения остаются разными:

- `ChatPanel` и часть shutdown adapters используют неограниченный `wait()` в
  GUI thread и способны заморозить закрытие окна;
- model downloader не отменяет каждый тип worker и может закрыть dialog при
  живом потоке;
- Book и Cover отменяют работу, но не гарантируют безопасное удержание до
  завершения;
- Live finalizer считает результат завершённым, даже если bounded wait ASR
  worker истёк.

Нужен один `WorkerRegistry`/`TaskSupervisor`: bounded GUI wait, disconnect
только business-сигналов, strong reference в `retired`, cleanup по встроенному
`QThread.finished`, exactly-once terminal outcome. Сетевой transport должен
уметь закрывать активный response/socket; один флаг Cancel между строками
потока не делает 600-секундное чтение прерываемым. `QThread.terminate()` не
использовать.

Acceptance: real-Qt тест с заблокированным fake transport, повторным Cancel и
закрытием окна; ни freeze, ни `QThread: Destroyed while thread is still
running`, ни поздний сигнал старого запуска не меняет новый UI state.

#### R2. Проверяемая загрузка моделей

Whisper/ONNX-модели сейчас принимаются по факту существования файла, а URL
может указывать на mutable revision. Для бинарника, который затем читает
native parser, этого недостаточно.

Нужен versioned manifest: immutable revision, ожидаемый размер и SHA-256;
streaming hash в `.download`, `fsync`, затем `os.replace`. Существующий файл
также проверяется перед использованием. При ошибке или Cancel временный файл
удаляется. Manifest и модельная лицензия должны попадать в release metadata.

Acceptance: bad digest, truncated existing file, network interruption и
повторный запуск не оставляют «валидную» модель и не повреждают старую.

#### R3. Backpressure и атомарность Recorder

Audio callback пишет в неограниченную queue, writer error только логируется,
а после пятисекундного join WAV может быть закрыт при ещё живом writer. При
медленном диске это даёт рост RAM, race и неполный файл, который UI принимает
за успешную запись.

Нужны bounded buffer и явная overflow policy, общий fatal state/signal,
детерминированный drain либо failure, запись во временный WAV и atomic
finalize. Метрики dropped frames следует показывать в diagnostics.

Acceptance: тесты slow writer, disk full и Cancel подтверждают ограниченную
память, один terminal outcome и отсутствие частичного «успешного» WAV.

#### R4. Довести защиту system-audio IPC

Случайный socket path, каталог `0700` и socket `0600` закрывают межпользовательский
доступ. Остаточный риск — процесс того же пользователя может перехватить
первое соединение или прислать некорректный размер payload.

Добавить криптографический nonce через inherited FD/env и обязательный
handshake, лимиты header/payload, а на macOS — проверку peer credentials, где
это доступно. Ошибка auth должна завершать helper до начала передачи PCM.

#### R5. Защитить артефакты от коллизий

Выходные каталоги и YouTube filenames опираются на `Path(source).stem`.
Файлы `interview.mp4` из разных каталогов могут перезаписать результаты или
смешать provenance.

Краткосрочно включить history record UUID в физический путь. Целевое решение
— Artifact entity и manifest с `record_id`, source hash/path, transcript
revision, type, provider/model/prompt version и timestamps. Запись каждого
артефакта — temp + atomic replace.

### P1 — структурный цикл

#### R6. Разделить `ui/main_window.py`

`MainWindow` превышает 1800 строк и одновременно является composition root,
document state, transcription/live/batch coordinator, history facade,
export controller и AI pipeline. Несколько ручных fan-out блоков результата
уже породили пропуск Cover; также окно напрямую обращается к приватным полям
дочерних widgets.

Рекомендуемая граница:

```text
domain/
  transcription.py       # Segment, Word, TranscriptionResult; без Qt/IO
  artifact.py             # Artifact, revision, provenance
  job.py                  # JobSpec, StepOutcome
application/
  document_session.py     # единый apply_result(result, source)
  transcription_controller.py
  pipeline_controller.py
  export_controller.py
infrastructure/
  asr/  ai/  media/  persistence/
ui/
  main_window.py          # только composition/navigation
  views/                  # публичные slots/signals, без доступа к _fields
```

Первый безопасный шаг — `DocumentSession.apply_result()` с одним fan-out и
тестом всех consumers. Затем по одному извлекать controllers, не переписывая
UI целиком.

#### R7. Очистить границы domain/application/infrastructure

`Segment`, `Word` и `TranscriptionResult` находятся в `transcriber.py`, который
импортирует Qt и worker infrastructure; exporters и Live тянут этот модуль
ради DTO. `Transcriber.prepare_models` динамически импортирует UI downloader,
что делает engine непригодным для headless use. `core.live.__init__` eagerly
реэкспортирует большую часть подсистемы и может загрузить платформенные/Qt
адаптеры при импорте контракта.

Вынести DTO в Qt-free `domain/transcription.py`, подготовку моделей — в
`ModelRepository` с progress/cancel contract, dialog оставить UI adapter.
Production-код должен импортировать конкретные `core.live.*` modules; package
`__init__` сделать пустым или полностью lazy.

#### R8. Один resumable Job/Pipeline Engine

Сейчас orchestration распределён между preset controller, extra-chain,
YouTube workers, Insights queue и двумя batch-механизмами. Если включены
YouTube и Insights, `chapters` вычисляются дважды.

Нужен DAG шагов с resource policy (`local_llm=1`, cloud provider-specific),
idempotence, persisted state, retry одного шага, Cancel и resume после crash.
Кэш артефакта учитывать transcript revision, язык, prompt version, provider
и model. Это устраняет повторные главы и сокращает число QThread.

#### R9. Единая release metadata и ресурсный manifest

Версии Windows/AppStream расходятся, Python package не имеет canonical
`__version__`, а packagers поддерживают ручные списки файлов. Создать один
`pyproject.toml`/version source и один manifest ресурсов/entry modules, из
которого генерируются PyInstaller, AppImage и platform metadata.

Добавить:

- macOS source + Qt + Swift-helper smoke и хотя бы nightly build `.app`;
- AppImage build/smoke на Linux;
- static test полного resource manifest для каждого packager;
- pin GitHub Actions по commit SHA, dependency hashes и SBOM;
- подписанный/notarized macOS release и Windows signing gate.

### P2 — качество, безопасность и производительность

#### R10. Схема и миграции Config

Loader фильтрует неизвестные поля, но не валидирует типы, enum и диапазоны.
Keyring API также не различает «секрет отсутствует» и «backend временно
сломался»; после такого чтения последующий save может затереть credential.

Добавить versioned schema/migrations, нормализацию URL/timeout/ranges и
tri-state secret lookup (`found`, `missing`, `backend_error`). На transient
error sentinel в config должен сохраняться до явного изменения поля.

#### R11. Безопасные Cover templates и cancellable frame extraction

Импортируемый template может ссылаться на absolute/`..` assets. Если JSON
считается переносимым пользовательским файлом, asset path должен после
`resolve()` оставаться внутри template root; нужны лимиты размера JSON,
изображений и path data. Frame extraction следует перевести с блокирующего
`subprocess.run` на cancellable `Popen` с timeout и cleanup.

#### R12. Явные ошибки вместо silent failure

Batch export сейчас может подавлять I/O failure, `LMStudioManager` недостаточно
валидирует JSON CLI, а book export без source способен выбрать неожиданный
current working directory. Возвращать structured outcome с частичными
ошибками, проверять JSON schema и передавать обязательный output directory
от app outputs/Save dialog/history metadata.

#### R13. Убрать лишний FTS rebuild

`HistoryStore` выполняет FTS rebuild при каждом init. Он нужен только при
создании, миграции или repair. Хранить schema version/repair marker и добавить
benchmark открытия истории на реалистичной базе.

#### R14. Усилить type- и real-Qt-gates

Снижать 33 UI mypy errors по модулю за раз и затем сделать `ui/` blocking.
Для worker lifecycle, closeEvent, model downloader, exports и fan-out нужны
real-Qt тесты: stubbed QThread не моделирует event queue, ownership и
уничтожение работающего объекта. Coverage публиковать раздельно для domain,
application и UI smoke, а не одной цифрой.

## 4. Рекомендуемый порядок структурных изменений

1. Закрыть R1–R5 и release smoke gates; не расширять фоновую обработку до
   единого lifecycle contract.
2. Ввести domain DTO и `DocumentSession` (R6–R7) без изменения поведения.
3. Реализовать Artifact model, затем Job Engine (R5, R8); мигрировать
   существующие генераторы по одному.
4. После стабилизации contracts унифицировать packaging/versioning (R9) и
   расширять продукт по отдельному продуктовому документу.

## 5. Definition of done для следующего аудита

- нет уничтожения/потери последней ссылки на running QThread;
- ни один success signal не испускается до проверки и атомарного сохранения;
- каждый Job/item имеет ровно одно terminal state;
- модель или внешний бинарный asset принимается только после integrity check;
- один transcript result распространяется через один публичный contract;
- все platform packages строятся из одного manifest и проходят launch smoke;
- README/ROADMAP содержат дату последней проверки, а датированные планы явно
  помечены snapshot/archive;
- unit, real-Qt, typing, dependency audit и packaging smoke являются
  воспроизводимыми CI gates.
