# Whispered: последовательный план исправления дефектов

Актуально для `HEAD 8f6613f` от 24 июля 2026 года.

## Статус выполнения (27 июля 2026)

- **Выполнено:** 1, 2, 3, 4, 8, 9, 10, 11.
- **Выполнено частично:** 5, 6, 13, 14, 15, 16, 17.
- **Не начато:** 7, 12.

Частично выполненные задачи ниже сохраняют точные незакрытые подпункты. Их
нельзя считать готовыми до выполнения критериев готовности и соответствующих
regression-тестов.

Этот файл предназначен для ИИ-агента, который будет исправлять продукт. Выполняй
задачи строго по порядку. Каждая задача — отдельный коммит. Не смешивай
рефакторинг соседних подсистем с текущим исправлением.

## Подтверждённое исходное состояние

- `ruff check .` — проходит.
- `python -m pytest tests/ -q` — `403 passed`.
- `python -m compileall -q . -x '.venv|.claude|build|dist'` — проходит.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check`
  — отрисовывает 64 состояния.
- Покрытие всего проекта — около 31%; все файлы `ui/*.py` имеют 0% покрытия
  обычным pytest-набором. Поэтому зелёный gate не проверяет найденные ниже
  сценарии с настоящим Qt.
- Исторический файл `Opus_4.6_review.md` уже частично отработан в текущем
  коммите. Не переносить из него задачи вслепую: проверять именно текущий код.

## Общие правила выполнения

1. Перед исправлением воспроизведи дефект тестом.
2. Для Qt-сценариев используй настоящий PyQt6 с
   `QT_QPA_PLATFORM=offscreen`, а не только заглушки из `tests/conftest.py`.
3. Ни один вызов длиннее примерно 100 мс не должен блокировать GUI-поток.
4. Не уничтожай `QThread`, пока он работает. Бизнес-сигнал с результатом не
   равен сигналу фактического завершения потока.
5. После каждой задачи запускай минимум:

   ```bash
   ruff check .
   python -m pytest tests/ -q
   python -m compileall -q . -x '.venv|.claude|build|dist'
   ```

6. После задач, затрагивающих UI, дополнительно запускай:

   ```bash
   QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
   ```

---

## 1. Добавить настоящий Qt regression harness

**Статус: выполнено.** Реализован отдельный `tests_qt/` с реальным PyQt6,
сессионным `QApplication`, smoke MainWindow и регрессией Replace All.

**Приоритет:** P0, инфраструктурная зависимость для следующих задач.

**Проблема:** обычные тесты подменяют PyQt6 заглушками, а весь `ui/` остаётся
неисполненным. Из-за этого не ловятся зависания, неверные связи сигналов,
рассинхронизация виджета с моделью и уничтожение активного `QThread`.

**Что сделать:**

- Добавить отдельный набор `tests_qt/` или маркированные `pytest`-тесты,
  запускаемые через `.venv/bin/python` и реальный PyQt6.
- Создать одну сессионную `QApplication`, вспомогательную функцию прокрутки
  event loop и безопасный teardown всех потоков/таймеров.
- Не подключать к этим тестам Qt-заглушки из `tests/conftest.py`.
- Добавить отдельную команду в `TESTING.md`; в конце плана сделать её CI-gate.

**Критерий готовности:** минимальный тест создаёт и закрывает `MainWindow`,
обрабатывает события и проверяет отсутствие активных дочерних `QThread`.

---

## 2. Исправить привязку записи из Library к исходному медиафайлу

**Статус: выполнено.**

**Приоритет:** P0, риск монтажа и экспорта не того файла.

**Файлы:** `core/history.py:211-256`, `ui/main_window.py:1104-1148`,
`ui/main_window.py:1185-1226`, `ui/file_selector.py:193-233`.

**Подтверждённый дефект:**

- `HistoryStore` хранит `source_path`, но `_load_from_history()` получает только
  `source_name`.
- При открытии записи не обновляются `_source_filepath`, `FileSelector` и
  `PlayerWidget`.
- Если до этого был выбран другой файл B, UI показывает транскрипт A, но
  проигрыватель, имя экспорта и `assemble_draft()` остаются привязаны к B.
- Если исходный файл A удалён, Cut всё равно объявляется доступным.

**Что сделать:**

- Добавить единый метод чтения полной записи с payload и метаданными
  (`source_path`, `source_name`, `model`, тип источника).
- В `_load_from_history()` атомарно переключать весь контекст записи:
  `_source_filepath`, плеер, имя экспорта, YouTube stem и Cut.
- Если `source_path` существует — загрузить его в плеер и разрешить Cut.
- Если файла нет или это transcript-only Live-запись — явно выгрузить старое
  медиа через `player.load("")`, очистить stale selection и отключить
  медиазависимые действия.
- `_open_record_view()` должен переходить на Record только если загрузка
  завершилась успешно; удалённая/битая строка не должна открывать предыдущий
  результат.
- Добавить явный сигнал очистки в `FileSelector`; очистка должна сбрасывать
  `_source_filepath`, состояние плеера и кнопку запуска.

**Тесты:**

- Выбрать B, затем открыть из истории A: во всех потребителях должен быть A.
- Открыть запись с отсутствующим source: старый B выгружен, Cut выключен.
- Открыть несуществующий `record_id`: остаёмся в Library.

---

## 3. Сделать редактирование транскрипта единым и сохраняемым

**Статус: выполнено.**

**Приоритет:** P0, видимые правки сейчас теряются или расходятся с экспортом.

**Файлы:** `ui/transcript_view.py:350-478`, `ui/transcript_view.py:493-540`,
`ui/main_window.py:930-985`, `core/history.py`.

**Подтверждённый дефект:**

- `Replace All` программно меняет `QTextEdit`, даже когда он read-only, но не
  меняет `TranscriptionResult`. Воспроизведение:
  виджет показывает `alpha GAMMA`, а `result.full_text` остаётся `alpha beta`.
- Поэтому Copy может вернуть одно, а Export/AI/History — другое.
- После выхода из Edit меняется объект result, но история не обновляется;
  после перезапуска правки и переименования спикеров исчезают.
- Chat хранит старую строку, а Insights/YouTube могут держать старый список
  сегментов. `_cleaned_text` и сгенерированные материалы также становятся
  устаревшими.
- Пересборка сегментов теряет `words` и может присвоить неверный `end` после
  удаления/перестановки строк.

**Что сделать:**

- Сделать `TranscriptionResult` единственным источником истины. Replace One/All
  должны менять сегментные тексты, а не только документ виджета.
- Либо разрешать замену только в Edit, либо реализовать корректное отображение
  результата сразу после операции.
- Добавить сигнал `result_changed` с типом изменения.
- На сигнал обновлять текущую запись в SQLite, speaker names, Chat,
  Insights/YouTube и Cut; сбрасывать зависимые cleaned/article-артефакты либо
  явно помечать их устаревшими.
- Добавить `HistoryStore.update_result(...)` в транзакции; не создавать новую
  строку истории на каждую правку.
- Сохранять прежние `end` и `words` при чисто текстовой правке. Для структурной
  правки определить детерминированные правила и предупреждать о невалидной
  строке вместо молчаливого удаления.
- В `_render_plain()` использовать `setPlainText`, чтобы текст вида `<tag>` не
  интерпретировался как rich text.

**Тесты:** replace/copy/export/history round-trip; rename speaker round-trip;
редактирование текста без потери исходных `end`/`words`; обновление контекста
Chat/Insights/YouTube.

---

## 4. Не терять word timestamps и тип источника в истории

**Статус: выполнено.**

**Приоритет:** P1, потеря части публичного контракта данных.

**Файлы:** `transcriber.py:69-100`, `core/history.py:76-96`,
`ui/main_window.py:1115-1129`, `ui/library_view.py:372-380`.

**Подтверждённый дефект:**

- `Segment.words` входит в основной контракт, но `_result_to_payload()` его не
  сериализует, а `_load_from_history()` не восстанавливает.
- Round-trip записи с `Word` возвращает ключи только `start/end/text/speaker`.
- Library вычисляет тип записи по имени. Live-сессия с микрофоном получает имя
  `REC_...wav` и попадает в фильтр Recorder, а обычный файл `zoom-...` ошибочно
  считается Live.

**Что сделать:**

- Версионировать history payload и сериализовать `words`.
- Восстанавливать `Word` с обратной совместимостью для старых строк.
- Добавить явное поле `source_kind` (`file`, `recorder`, `live`) в схему/metadata
  и перестать определять тип по имени.
- Написать безопасную миграцию без переписывания пользовательских
  транскриптов; для старых строк оставить документированный fallback.

**Критерий готовности:** полный `TranscriptionResult` проходит add/get/load
round-trip без потери полей; Live с mic отображается в фильтре Live.

---

## 5. Исправить запись с микрофона без ложного `file_ready` и перезаписи файлов

**Статус: выполнено частично.** Исправлены пустой `file_ready`, уникальность
имени и остановка Recorder при закрытии. Осталось исключить race между writer
thread и закрытием WAV при истечении `join(timeout=5)`.

**Приоритет:** P0, риск нулевого файла и перезаписи записи.

**Файлы:** `core/recorder.py:105-208`, `ui/recorder_widget.py:136-189`,
`ui/main_window.py:184-228`.

**Подтверждённый дефект:**

- При ошибке открытия устройства `Recorder` уже создал WAV и сохранил
  `_output_path`.
- Синхронный `error_occurred` вызывает `RecorderWidget._on_error()`, тот вызывает
  `_stop_recording()`, а `stop()` возвращает путь. В результате одновременно
  эмитятся ошибка и `file_ready` для нулевого/несуществующего WAV.
- Имя имеет точность до секунды (`REC_%Y-%m-%d_%H-%M-%S.wav`); два быстрых
  старта или два Recorder могут открыть один путь с `"wb"` и перезаписать файл.
- `MainWindow.closeEvent()` не останавливает обычную активную запись.

**Что сделать:**

- `stop()` должен возвращать путь только для реально начавшейся валидной записи
  с записанными кадрами; путь ошибки удалить или пометить как failed.
- `_on_error()` должен сбрасывать UI без эмита `file_ready`.
- Генерировать уникальное имя атомарно (`microseconds`/UUID + exclusive create).
- При закрытии окна корректно остановить запись, дождаться writer thread и
  закрыть WAV. Решить продуктово, сохранять ли незавершённую запись, и покрыть
  это тестом.
- Если writer thread не завершился за timeout, не закрывать файл у него под
  ногами: сообщить ошибку и выполнить безопасный bounded shutdown.

---

## 6. Перенести подготовку моделей в корректный поток и починить downloader

**Статус: выполнено частично.** Проверка/диалоги моделей перенесены до старта
BatchWorker в GUI-поток, а `setup.sh` использует runtime models directory.
Остались отмена/ожидание downloader workers, очистка `.download` во всех
ошибочных ветках и полноценная проверка готовности модели.

**Приоритет:** P0, Qt-crash в Batch и незавершаемые загрузки.

**Файлы:** `transcriber.py:605-648`, `batch_processor.py:114-163`,
`ui/model_downloader.py:18-277`, `setup.sh`, `setup-mac.sh`.

**Подтверждённый дефект:**

- `BatchWorker` вызывает `Transcriber.transcribe()` из своего `QThread`.
  `transcribe()` при отсутствии модели создаёт и запускает `QDialog`. QWidget
  создаётся не в GUI-потоке — это недопустимо.
- `ensure_diarization_models()` показывает диалог для каждого batch item и не
  имеет реальной проверки кеша.
- Cancel у `ModelDownloaderDialog` сразу закрывает dialog, но не ждёт
  `DownloadWorker`; `DiarizationCacheWorker` вообще не отменяется. Возможен
  `QThread: Destroyed while thread is still running`.
- Существование файла считается достаточной валидацией: нулевой/оборванный
  `.bin` принимается как готовая модель.
- `setup.sh` и `setup-mac.sh` скачивают `base` в `<repo>/models`, а runtime ищет
  модели через `core.paths.models_dir()` (на macOS это
  `~/Library/Application Support/Whispered/models`). Кроме того, default config
  выбирает `large-v3-turbo-q5_0`, а setup скачивает `base`.

**Что сделать:**

- В GUI-потоке до старта одиночной/пакетной/Live-операции выполнить единую
  асинхронную фазу `prepare_models`.
- Сам downloader оставлять в worker, но все QWidget/dialog операции выполнять
  только в main thread.
- Один раз подготовить модели на весь batch, а не на каждый item.
- Реализовать закрытие HTTP response, cleanup `.download`, checksum или хотя бы
  проверку ожидаемого минимального размера/формата и атомарный `os.replace`.
- Cancel должен дождаться фактического завершения worker без блокировки UI;
  для pyannote либо сделать поддержанную отмену, либо запретить закрывать dialog
  до безопасной точки с понятным статусом.
- Setup должен использовать тот же `models_dir()` и скачивать выбранную
  default-модель либо вообще не делать лишнюю предзагрузку.

**Тесты:** отсутствующая модель в Batch не создаёт QWidget из worker thread;
cancel не оставляет QThread/partial; битая модель перекачивается; setup/runtime
пути совпадают.

---

## 7. Сделать отмену и закрытие окна неблокирующими и безопасными

**Статус: не начато.**

**Приоритет:** P0, зависание GUI до 5–10 минут и Qt-crash.

**Файлы:** `ui/main_window.py:184-228`, `ui/main_window.py:853-881`,
`transcriber.py:615-677`, `batch_processor.py:319-328`,
`ui/chat_panel.py:174-180,264-310`, `core/lm_client.py`,
`ui/youtube_panel.py:231-258`, `ui/insights_panel.py:198-217`,
`ui/main_window.py:1434-1471`.

**Подтверждённый дефект:**

- UI-обработчики вызывают `wait()` без timeout для транскрипции, AI, Chat и
  Batch.
- Нестриминговый `urllib`-запрос проверяет cancel только до отправки и может
  ждать `DEFAULT_TIMEOUT=300`; Insights ждёт stream до 600 секунд.
- `TranscriptionWorker` при kill имеет собственные ожидания до 8 секунд, а
  вызывающий GUI всё это время заблокирован.
- Активный `DraftAssemblyWorker` не отменяется и не ожидается в `closeEvent`.
- `batch_finished`/AI result-сигналы эмитятся из `run()` до фактического
  завершения QThread; ссылки местами обнуляются сразу. Это гонка уничтожения
  работающего потока.

**Что сделать:**

- Ввести единый lifecycle: `request_cancel()` возвращает сразу; UI показывает
  Cancelling; окончательный cleanup идёт по фактическому завершению потока.
- Не использовать unbounded `wait()` в GUI. При закрытии — bounded
  последовательность cancel → timeout → безопасный hard-stop только для
  subprocess, но не `QThread.terminate()`.
- Обеспечить прерываемый HTTP transport: короткий read timeout с циклом,
  закрытие response/socket или другой клиент с cooperative cancellation.
- Не затенять встроенный `QThread.finished` бизнес-сигналами с тем же именем;
  переименовать их в `result_ready`, `download_completed` и т.п.
- Удалять worker через `deleteLater()` только после настоящего завершения.
- Добавить cancel для ffmpeg draft: хранить `Popen`, отправлять terminate/kill
  bounded, удалять неполный output.

**Критерий готовности:** Cancel и close возвращают управление event loop менее
чем за 100 мс; через bounded время нет активных процессов/потоков и partial
output.

---

## 8. Гарантировать ровно один terminal event от процесса транскрипции

**Статус: выполнено.** Добавлен явный terminal sentinel; удалено
использование ненадёжного `multiprocessing.Queue.empty()`.

**Приоритет:** P1, редкое вечное состояние Processing.

**Файл:** `transcriber.py:487-566`.

**Проблема:** после смерти дочернего процесса код дренирует
`multiprocessing.Queue` через `q.empty()`. Документация multiprocessing не
гарантирует надёжность `empty()` между процессами. Возможна гонка: child уже
завершился, feeder ещё не сделал сообщение видимым, `empty()` вернул True,
exitcode равен 0 — worker заканчивается без `finished` и без `error`. UI или
Batch остаётся ждать terminal callback.

**Что сделать:**

- Ввести явный протокол terminal message (`result`, `error`, `cancelled`) и
  гарантировать один terminal event.
- Не использовать `Queue.empty()` для синхронизации.
- После завершения child сделать bounded drain/join с корректной обработкой
  отсутствующего terminal message как ошибки протокола.
- Закрывать queue/join feeder во всех ветках.

**Тесты:** настоящий spawn-child для result/error/crash/cancel и искусственная
задержка доставки queue; каждый сценарий даёт ровно один terminal signal.

---

## 9. Сериализовать запросы к локальному LM Studio

**Статус: выполнено.**

**Приоритет:** P0, текущий код сам провоцирует зависание локального сервера.

**Файлы:** `ui/youtube_panel.py:270-315`, `ui/insights_panel.py:224-256`,
`ui/main_window.py:986-1030`, `core/lm_client.py`.

**Подтверждённый дефект:**

- YouTube одновременно запускает 5 `InsightsWorker`.
- Insights одновременно запускает ещё 3 worker.
- Full preset параллельно запускает YouTube и Text cleaning. Chat/Book могут
  добавить новые локальные запросы.
- Это прямо противоречит проектному ограничению из `CLAUDE.md`: длинные
  параллельные запросы могут подвесить LM Studio.

**Что сделать:**

- Ввести общий process-wide диспетчер локальных LLM-задач с concurrency=1.
- Очередь должна поддерживать приоритет интерактивного Chat, отмену ещё не
  начатой задачи, прогресс и корректное завершение группы.
- Не сериализовать без необходимости независимые cloud-запросы, но ограничить
  их разумным configurable concurrency.
- YouTube/Insights должны запускать следующий тип после terminal event
  предыдущего, сохраняя частичные успешные результаты.

**Тесты:** Full preset никогда не имеет более одного активного локального
запроса; cancel очищает очередь; одна ошибка не оставляет pending-count
навсегда.

---

## 10. Не выдавать заглушки AI за успешные артефакты

**Статус: выполнено.**

**Приоритет:** P1, ложный успех и сохранение ошибочных файлов.

**Файлы:** `article_generator.py:357-455`,
`article_generator.py:457-509`, `ui/main_window.py:986-1070`.

**Подтверждённый дефект:** если client всегда возвращает `None`,
`generate_all_formats()` успешно возвращает пять Article с title
`"Generation Failed"` и текстом `"Unable to generate article..."`.
Preset после этого считает шаг успешным, сохраняет эти пять файлов, добавляет
history chip `article` и может показать success toast.

**Что сделать:**

- Разделить успешный контент, fallback и ошибку транспорта явным result-типом
  либо исключениями доменного уровня.
- Не добавлять failed Article в `GenerationResult.articles`.
- Preset должен считать article-этап успешным только при наличии реального
  контента; не сохранять error text как пользовательский артефакт.
- Отдельно сообщать partial success с количеством форматов и причинами ошибок.

**Тесты:** offline, пустой ответ, один из пяти упал, cancel, JSON topic parse
fallback. История и число сохранённых файлов должны совпадать только с
реальными успехами.

---

## 11. Синхронизировать визуальный порядок Batch с моделью

**Статус: выполнено.**

**Приоритет:** P1, обрабатывается не тот порядок и обновляется не та строка.

**Файлы:** `ui/batch_panel.py:146-149`, `ui/batch_panel.py:205-216`,
`ui/batch_panel.py:299-308`, `batch_processor.py:93-105`.

**Подтверждённый дефект:** `QListWidget` разрешает `InternalMove`, но
`BatchProcessor._items` не переставляется. После drag-and-drop пользователь
видит новый порядок, worker обрабатывает старый, а index-сигналы могут обновлять
чужой визуальный item.

**Что сделать:**

- Либо временно выключить reorder, либо обработать `rowsMoved` и атомарно
  переставлять `_items`.
- Не хранить индекс внутри `BatchItemWidget` как постоянную identity; ввести
  стабильный item id/path mapping и пересчитать remove callbacks после move.
- Запретить reorder во время обработки.

**Тесты:** переставить B перед A, запустить batch, проверить порядок start и
соответствие progress/error нужной строке.

---

## 12. Довести Live lifecycle до терминального состояния

**Статус: не начато.**

**Приоритет:** P1, утечки ASR process и зависание состояния.

**Файлы:** `core/live/runtime.py:88-166`, `core/live/runtime.py:188-205`,
`core/live/runtime.py:251-292`, `ui/main_window.py:492-507`.

**Подтверждённые проблемы:**

- Если последний источник падает после асинхронного старта helper,
  `_source_error()` ставит SessionState.FAILED, но не вызывает `cancel()`.
  ASR worker может продолжить жить.
- После этого `is_running()` уже False; повторный `start()` очищает словари и
  перезаписывает `_worker`, не дожидаясь старого процесса.
- `_drainers` не очищается между сессиями.
- `_finish_session()` после 10-секундного timeout всё равно строит результат;
  поздний ASR result может потеряться. Проверка scheduler stats и обновление
  timeline не защищены одним completion barrier.
- `LivePreflightWorker` после завершения не очищается через `deleteLater()` и
  накапливается как child `MainWindow`.

**Что сделать:**

- Сделать идемпотентный единый teardown для completed/failed/cancelled.
- При падении всех источников отменять ASR, закрывать adapters, join drainers и
  только затем разрешать новый start.
- Очищать per-session коллекции после teardown.
- Ввести явный scheduler drain barrier; при timeout помечать результат
  incomplete и сообщать dropped/pending, а не молча объявлять completed.
- Удалять preflight workers по фактическому `QThread.finished`.

**Тесты:** helper launch failure, mic+system с падением одного источника,
падение обоих, повторный start после failure, stop при in-flight decode.

---

## 13. Исправить экспорт: экранирование, частичные ошибки и атомарная запись

**Статус: выполнено частично.** Исправлены VTT/PDF escaping, атомарная запись
через `export_result` и сообщения о частичных ошибках в Record. Осталось
сделать то же для `BatchProcessor.export_all` и добавить все regression-тесты.

**Приоритет:** P2.

**Файлы:** `exporters.py:40-262`, `ui/main_window.py:1185-1226`,
`batch_processor.py:335-376`.

**Проблемы:**

- PDF экранирует только `<` и `>` в тексте, не экранирует `&` и имя спикера.
- VTT не экранирует пользовательский cue text; `<v ...>` из транскрипта меняет
  структуру cue.
- Multi-export и batch export глотают исключения и показывают общий success
  даже при нуле или частичном наборе файлов.
- Экспорт пишет прямо в конечный путь и может оставить усечённый файл поверх
  существующего при ошибке.

**Что сделать:**

- Использовать корректное format-specific escaping.
- Писать во временный файл рядом с target, flush/fsync при необходимости и
  заменять через `os.replace` после успеха.
- Возвращать структурированный отчёт `{created, failed}`; UI должен показать
  названия и причины неудачных форматов.
- Не показывать success при `created == 0`.

**Тесты:** специальные HTML/VTT-символы в тексте и speaker name; ошибка одного
формата; сохранность существующего target при сбое.

---

## 14. Сделать config сохранение атомарным, приватным и валидируемым

**Статус: выполнено частично.** Запись приватна с момента создания временного
файла и атомарно заменяет старый config. Остались type/range validation и
предсказуемое восстановление после повреждённого JSON.

**Приоритет:** P2, сохранность настроек и API-ключей.

**Файл:** `config.py:79-135`.

**Проблемы:**

- Новый `config.json` сначала создаётся обычным `open("w")`, и лишь затем
  получает `chmod 0600`. При permissive umask есть окно с более широкими
  правами.
- Запись неатомарна: crash/полный диск оставит битый JSON, после чего load
  молча вернёт все defaults.
- Loader фильтрует только имена полей, но не типы, диапазоны и enum. Битое
  `export_formats`, URL, отрицательные context limits и т.п. падают позже в UI.

**Что сделать:**

- Создавать temp с mode `0600`, записывать JSON, flush, затем `os.replace`.
- При невалидном текущем файле сохранять `.corrupt-<timestamp>` и показывать
  пользователю понятное предупреждение.
- Валидировать/нормализовать каждое поле; неизвестные значения заменять
  default точечно, не сбрасывая весь config.
- Добавить тесты POSIX mode, interrupted write и повреждённых отдельных полей.

---

## 15. Починить установку и пакеты macOS/Linux

**Статус: выполнено частично.** Исправлен путь моделей в `setup.sh`; задачи
macOS helper и полный AppImage manifest/smoke ещё не выполнены.

**Приоритет:** P1 для распространяемых сборок.

**Файлы:** `build.py`, `core/live/preflight.py:84-87`,
`appimage/build-appimage.sh`, `appimage/whispered.desktop`,
`requirements*.txt`, `setup.sh`, `setup-mac.sh`.

**Подтверждённые дефекты:**

- macOS `build.py` не копирует и не подписывает
  `whispered-capture-helper`. В frozen app `default_helper_path()` ищет его
  внутри bundle/runtime tree, где файла нет. System audio работает только из
  локального source checkout с заранее выполненным `swift build`.
- AppImage не копирует `book_pipeline.py`, `timeline_export.py`,
  `video_edit.py`, `video_cut.py`, `video_input.py`, хотя `main.py` импортирует
  их на старте.
- AppImage также не копирует `locales/`, `prompts/`, `assets/` и не включает
  `sounddevice`/`python-docx`.
- AppImage создаёт launcher `whisper-fedora`, а desktop entry объявляет
  `Exec=whispered`; имена icon/output glob тоже расходятся с Whispered.

**Что сделать:**

- Для macOS собирать helper, класть в стабильный resource/helper path bundle,
  подписывать вместе с приложением и резолвить через `resource_path`.
- Если helper не может войти в конкретную сборку — скрыть/отключить system
  capture с честным compile-time capability, а не показывать неработающий flow.
- Перестроить AppImage packaging вокруг явного полного manifest или PyInstaller,
  чтобы динамические импорты и ресурсы не пропускались.
- Унифицировать binary/desktop/icon/AppImage names.
- Добавить frozen smoke, который не только создаёт окно, но импортирует каждый
  top-level workflow и проверяет наличие ресурсов/helper.

**Критерий готовности:** чистая macOS-сборка проходит preflight system helper;
чистый AppImage запускается без source tree и открывает Transcribe, Queue,
Recorder, Book, Cut и локализованные prompts.

---

## 16. Закрыть мелкие, но видимые дефекты Library и диагностики

**Статус: выполнено частично.** Исправлены UTC→local форматирование и битый
`artifacts` JSON. Остались cleanup preflight workers и локализация веток.

**Приоритет:** P3, выполнять после критических изменений модели данных.

**Файлы:** `ui/library_view.py:41-46`, `core/history.py:117-131`,
`ui/main_window.py:492-507`.

**Что сделать:**

- `created_at` сохраняется в UTC, но `_fmt_date()` форматирует его без
  `astimezone()`. В UTC+3 запись на `12:00Z` показывается как `12:00`, а не
  `15:00`. Конвертировать aware datetime в локальную зону.
- Защитить `HistoryRecord.artifacts` от битого JSON одной строки, чтобы вся
  Library не становилась пустой.
- Очищать законченные preflight/check worker-объекты и не держать старые
  ссылки.
- Локализовать оставшиеся пользовательские строки в новых error/status ветках.

**Тесты:** UTC→local с подменённой TZ; одна строка с битым `artifacts` не мешает
показать остальные записи.

---

## 17. Сделать строгие gates обязательными

**Статус: выполнено частично.** Добавлены real-Qt suite и документация; CI
ещё не запускает их, а mypy пока informational.

**Приоритет:** P1 после исправления перечисленных дефектов.

**Файлы:** `.github/workflows/ci.yml`, `requirements-dev*.txt`, `TESTING.md`.

**Проблема:** текущий mypy-check informational и на актуальной команде выдаёт
28 ошибок. Real-Qt smoke/gallery не запускаются в Linux CI. Поэтому ошибки
границ типов и практически весь UI остаются вне merge gate.

**Что сделать:**

- Исправить текущие mypy-ошибки хотя бы в `core/`, `transcriber.py`,
  `diarizer.py`, `exporters.py`, `utils.py`, `config.py`.
- Убрать `continue-on-error` у ограниченного стабильного mypy target.
- Добавить Linux job с runtime Qt dependencies, `QT_QPA_PLATFORM=offscreen`,
  новым Qt regression suite и `render_ui_gallery.py --check`.
- Добавить smoke для multiprocessing terminal protocol.
- Зафиксировать минимальный coverage threshold отдельно для core и нового
  Qt-набора; не использовать общий 31% как достаточный критерий.

## Финальный release gate

После всех задач агент должен выполнить и приложить результат:

```bash
ruff check .
python -m pytest tests/ -q
python -m compileall -q . -x '.venv|.claude|build|dist'
python -m mypy --ignore-missing-imports core/ transcriber.py diarizer.py exporters.py utils.py config.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests_qt/ -q
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_ui_gallery.py --check
```

Дополнительно:

- чистый macOS frozen smoke с собранным helper;
- чистый Linux AppImage smoke вне checkout;
- ручные сценарии из `TESTING.md`: cancel/close для Transcribe, Queue,
  Recorder, AI preset и Live;
- проверка, что после каждого сценария нет активных `QThread`, child process,
  ffmpeg/helper process и временных `.download`/частичных export-файлов.
