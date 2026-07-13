# План для Sonnet: исправление багов и техдолга (ревизия 2026-07-14)

> **Контекст.** Whispered — privacy-first десктоп-приложение транскрипции на
> PyQt6 (whisper.cpp + локальный LM Studio; опциональное облако только для
> YouTube-вкладки). Проведена полная ревизия проекта. Этот документ **заменяет**
> `docs/plans/AUDIT_FIXES_PLAN.md` (тот перемещён в `docs/archive/`): все его
> актуальные пункты (B1–B11) перепроверены по коду 2026-07-14, подтверждены и
> включены сюда, плюс добавлены новые находки (N1–N9).
> Документ самодостаточен: читай целиком до начала работы.
> Стиль кода и правила — как в `ROADMAP.md` §1.

---

## 0. Сводка задач

| № | Серьёзность | Где | Суть |
|---|---|---|---|
| F1 | **Высокая** | `tests/` (нет `conftest.py`) | Полный прогон `pytest tests/` **зависает навсегда** — сьют порядко-зависим |
| F2 | **Высокая** | `core/insights_worker.py:26`, `core/llm_text.py` | Транскрипт обрезается по хвосту на 48 000 символов → тайм-коды не покрывают конец видео |
| F3 | **Высокая** | `core/lm_client.py:21`, `core/insights_worker.py` | `max_tokens=4096` обрезает кириллические JSON-ответы посреди строки → парсинг падает |
| F4 | **Высокая** | `ui/main_window.py:75` (`closeEvent`) | Закрытие окна не останавливает воркеры YouTube/Insights/Chat → «QThread: Destroyed while thread is still running» |
| F5 | **Высокая** | `ui/youtube_panel.py`, `ui/main_window.py` | Язык «Авто» → `language=None` → нет языковой директивы → модель отвечает на английском |
| F6 | Средняя | `ui/youtube_panel.py` | Сигналы старых воркеров не отсоединяются → «протухший» воркер ломает счётчик `_pending` нового прогона |
| F7 | Средняя | `ui/youtube_panel.py:_on_error` | Ошибки генерации молча проглатываются; текст всегда «LM Studio did not respond» даже для облака |
| F8 | Средняя | `ui/provider_dialog.py:_on_accept:109` | Диалог пишет `yt_provider` из своего комбо → расхождение с комбобоксом панели |
| F9 | Средняя (безопасность) | `config.py:154+` (`__main__`) | `python config.py` печатает `yt_openai_api_key`/`yt_anthropic_api_key` открытым текстом |
| F10 | Средняя | `ui/youtube_panel.py:28` | `_OUTPUT_DIR` внутри бандла PyInstaller — read-only, сохранение сломается |
| F11 | Средняя | `transcriber.py:590-598` | `get_available_models()` ищет `ggml-large.bin` и т.п., а реальные файлы — `ggml-large-v3-turbo-q8_0.bin` → всегда пустой список |
| F12 | Низкая | `core/youtube_description.py` | Нет проверки «≥10 с между главами» — YouTube молча отключает главы |
| F13 | Низкая | `ui/youtube_panel.py:_generate` | Завершённые QThread не удаляются (`deleteLater` нет) → накопление объектов |
| F14 | Низкая | `zoom_to_blog.py`, `run_workflow.sh` | Legacy-пайплайн на **другом движке** (openai-whisper CLI, не whisper.cpp) — дублирует и путает |
| F15 | Низкая | везде | Остатки «Whisper Fedora» в заголовках модулей, `build.py`, иконках; каталог данных `~/.whisper-fedora` |
| F16 | Низкая | `ui/main_window.py` и др. | Захардкоженные строки мимо i18n: «Starting batch processing...», «Нет транскрипта для обработки», тултипы |
| F17 | Низкая | разное | Мелочь из старого аудита: бессмысленный `test_api_key_not_logged`; дублирование `_TAB_KEYS`/`_edit_map`; тост с длинным абсолютным путём; склейка «описание+тайм-коды» не покрыта тестами |

---

## 1. Обязательные правила

1. **Офлайн — дефолт.** Ничего не менять в поведении по умолчанию
   (`yt_provider="lmstudio"`); облако — только явный выбор пользователя.
2. **Тесты без сети.** Мокать `urllib`/клиентов. После F1 стаб-паттерн Qt
   живёт в `tests/conftest.py` — новые тесты используют его, а не свои копии.
3. **Не трогать** пайплайн транскрипции (`_run_transcription_process`),
   `prompts/*.md`, существующие типы инсайтов.
4. **Не ломать вызывающих.** Новые параметры — только опциональные с
   безопасными дефолтами.
5. **Никогда не логировать ключи** — ни целиком, ни частично, ни в ошибках.
6. **Один шаг — один коммит.** Формат: `fix:` / `refactor:` / `test:` / `chore:`.
7. **Проверка перед каждым коммитом:** `ruff check .` (0 ошибок),
   `perl -e 'alarm 120; exec @ARGV' python -m pytest tests/ -q` (зелёный и
   **завершается** — после F1 это обязательное условие), `python -m compileall`
   изменённых файлов, headless-смоук:
   `QT_QPA_PLATFORM=offscreen .venv/bin/python -c "...создать MainWindow..."`.
   Юнит-тесты гоняются системным python (PyQt6 не установлен — стабы);
   смоук — `.venv/bin/python` (там реальный PyQt6).

---

## 2. Задачи (в порядке выполнения)

### F1. Починить зависание pytest: единый conftest.py

**Диагноз (проверен 2026-07-14).** Каждый тест-файл сам вставляет стабы
PyQt6 в `sys.modules` через `setdefault` + свои `QThread`/`pyqtSignal`.
Побеждает тот стаб, чей файл импортировался первым; при полном прогоне
комбинация `tests/test_ai_provider.py` + `tests/test_insights_worker_provider.py`
намертво вешает процесс (воспроизводится:
`python -m pytest tests/test_ai_provider.py tests/test_insights_worker_provider.py`).
Отдельно каждый файл проходит. Это значит **CI, скорее всего, тоже висит или
нестабилен** — проверь workflow после фикса.

**Что сделать.**
1. Создать `tests/conftest.py` с одним каноничным набором стабов:
   - модули `PyQt6`, `PyQt6.QtCore`, `PyQt6.QtWidgets`, `PyQt6.QtGui`,
     `PyQt6.QtMultimedia`;
   - `QThread` с `__init__(self, parent=None)`, `start` (синхронно зовёт
     `self.run()` — тогда воркеры тестируются детерминированно), `isRunning`
     → `False`, `wait` → `True`, `deleteLater` → no-op, `cancel` не нужен
     (он в `BaseWorker`);
   - `pyqtSignal` — дескриптор с `connect/emit` (взять реализацию
     `_FakeSignal`/`_BoundSignal` из `tests/test_insights_worker_provider.py`,
     она самая полная);
   - `QObject` с принимающим всё `__init__`.
2. Удалить из **всех** тест-файлов локальные `sys.modules`-манипуляции
   (`setdefault`, `pop`, присвоения `_qtcore.*`) — оставить только импорты.
   Файлы: `test_ai_provider.py`, `test_insights_worker_provider.py`,
   `test_workers.py`, `test_text_processor.py`, и другие — найди все через
   `grep -l "sys.modules" tests/`.
3. Стабы для `core.lm_client` в тестах делать через `monkeypatch.setattr`
   на уже импортированный модуль, а не подменой модуля в `sys.modules`.

**Приёмка:** `python -m pytest tests/ -q` завершается < 30 с, зелёный;
трижды подряд с `-p no:randomly` и без — стабильно. CI-workflow обновлён,
если он полагался на старое поведение.

### F2. Обрезка транскрипта: хвост важнее лимита

`_TRANSCRIPT_MAX_CHARS = 48_000` в `core/insights_worker.py` — обрезка по
хвосту через `fit_to_context`. Измерено ранее: видео 48:41 → модель не видела
последние 7 минут; для 2-часовых записей потеряется половина.

**Что сделать (вариант «равномерное прореживание», предпочтителен):**
- В `_build_prompt_text` при превышении лимита **не отрезать хвост**, а
  прореживать сегменты равномерно по всей длительности: сортировка по start,
  выбор каждого k-го сегмента так, чтобы суммарная длина ≤ лимита, но первые
  и последние ~20 сегментов сохранить всегда (начало задаёт контекст, конец
  нужен для последних глав).
- Лимит вынести в параметр `Config.insights_context_chars` (дефолт 48 000,
  редактируемый в настройках AI) — у пользователей с большим контекстом
  локальной модели (46k токенов у gemma) лимит может быть выше.

**Приёмка:** юнит-тест — сегменты 0..7200 с, суммарно >лимита → в промпт
попадают и сегменты из последних 5 минут, и из первых; общая длина ≤ лимита.

### F3. Обрезка ответа модели: поднять max_tokens и детектировать обрыв

**Диагноз (воспроизведён вживую 2026-07-14).** Генерация `yt_description`
на русском упёрлась в `DEFAULT_MAX_TOKENS = 4096` (`core/lm_client.py:21`) —
кириллица токенизируется неэффективно, JSON оборвался посреди строки, парсинг
упал, ретрай тоже. Починилось поднятием до 8000.

**Что сделать:**
1. В `InsightsWorker._execute` передавать `max_tokens=8000` явно (или добавить
   в `_INSIGHT_TYPES`-специфичные лимиты — описанию и главам нужно больше, чем
   тегам).
2. В `LMStudioClient.chat_completion_stream` парсить `finish_reason` из
   SSE-чанков; если `length` — логировать warning «response truncated by
   max_tokens». Не менять возвращаемое значение (обратная совместимость).
3. `AnthropicClient` — аналогично по полю `stop_reason == "max_tokens"`.

**Приёмка:** юнит-тест: мок SSE с `finish_reason: length` → warning в логе
(caplog), текст всё равно возвращён.

### F4. closeEvent: останавливать все воркеры

`MainWindow.closeEvent` (строка 75) останавливает transcriber, `_ai_worker`,
batch и book, но **не** трогает:
- воркеры `youtube_panel` (`_workers` dict);
- воркеры `insights_panel`;
- воркер `chat_panel` (`_worker`).

**Что сделать:** в `closeEvent` перед `event.accept()` вызвать
`self.youtube_panel.clear()`, `self.insights_panel.clear()` и добавить в
`ChatPanel` публичный `shutdown()` (cancel + wait), позвать его.
`clear()` обеих панелей уже делает cancel/wait — переиспользуй.

**Приёмка:** headless-смоук — создать окно, запустить фейковый воркер
(заглушка со sleep), закрыть окно → нет «QThread: Destroyed while thread is
still running» в stderr.

### F5. Язык «Авто» → язык транскрипта, а не отсутствие директивы

При «Авто» в комбо (`youtube_panel`, `insights_panel`) передаётся
`language=None` → в промпте нет директивы → модель часто отвечает на
английском для русского транскрипта (воспроизводилось).

**Что сделать:** «Авто» должен резолвиться в язык **транскрипта**:
`TranscriptionResult.language` уже есть (whisper возвращает "ru"/"en").
Прокинуть его в панели через `set_segments(segments, language=...)` (новый
опциональный параметр) или отдельный `set_language()`. Маппинг кода в
название для промпта: `{"ru": "Russian", "en": "English", ...}` — достаточно
топ-15 языков, fallback — код как есть.

**Приёмка:** юнит-тест маппинга; ручная проверка — русский транскрипт,
язык «Авто», главы на русском.

### F6. Отсоединять сигналы старых воркеров (образец — insights_panel)

В `youtube_panel._generate` и `clear()` перед сбросом `self._workers`
отсоединить `finished`/`error_occurred` каждого старого воркера
(`try: w.finished.disconnect(...) except TypeError: pass`) — как уже сделано
в `ui/insights_panel.py` (~строки 210–218). Иначе воркер, не успевший
завершиться за `wait(1000)`, позже эмитит сигнал в новый прогон и ломает
`_pending`/содержимое вкладок.

**Приёмка:** юнит-тест с FakeSignal: старый воркер эмитит `finished` после
нового `_generate` → счётчик и вкладки нового прогона не затронуты.

### F7. Показывать ошибки генерации пользователю

`_on_error` в `youtube_panel` только логирует; вкладки пустые, пользователь
не понимает, что случилось; сообщение всегда «LM Studio did not respond»,
даже когда провайдер — облако (`insights_worker.py:135-138`).

**Что сделать:**
1. В `InsightsWorker._execute` формировать сообщение по провайдеру:
   `f"{provider_label} did not respond"` (LM Studio / OpenAI-compatible /
   Anthropic).
2. В `youtube_panel._on_error` писать текст ошибки в соответствующую вкладку
   (`youtube_error` + сообщение) и показывать toast «kind=error» один раз
   за прогон (не 5 тостов).
3. Если завершились все воркеры и **хоть один** успешен — включать
   «Копировать»/«Сохранить» (сейчас при последнем-упавшем кнопки остаются
   выключенными).

**Приёмка:** юнит-тест: 4 успеха + 1 ошибка → кнопки включены, вкладка с
ошибкой содержит текст ошибки.

### F8. Синхронизировать провайдера: диалог ↔ комбобокс панели

`ProviderDialog._on_accept` пишет `cfg.yt_provider` из своего комбо;
комбобокс панели не обновляется → UI показывает одно, генерация идёт через
другое.

**Что сделать:** самое простое — диалог **не трогает** `yt_provider` вовсе
(он настраивает креды выбранного вида), поле `_kind_combo` в диалоге лишь
переключает видимые поля. Панель остаётся единственным владельцем
`yt_provider`. Если оставляешь запись — после `dialog.exec()` в
`_open_provider_dialog` перечитать конфиг и выставить комбо панели.

**Приёмка:** открыть диалог с провайдером openai, переключить в нём на
anthropic, нажать OK → комбо панели и `cfg.yt_provider` согласованы.

### F9. Маскировать все секреты в `python config.py`

В `__main__`-блоке `config.py` маскируется только `hf_token`. Сделать
общий подход: маскировать любое поле, имя которого содержит `token`/`key`
(`if value and ("token" in key or "key" in key.lower())`). 

**Приёмка:** юнит-тест не обязателен; ручная проверка `python config.py`
с заполненными ключами → в выводе только `sk-...`-огрызки или `***`.

### F10. Каталог сохранения YouTube-файлов — в пользовательские данные

`_OUTPUT_DIR = Path(__file__)…/output` ломается в PyInstaller-бандле
(read-only). Заменить на `core.paths.data_dir() / "output"` c fallback;
в тосте показывать `~/…`-сокращённый путь (заодно закрывает часть F17).
Дать кнопку/ссылку «Открыть папку» (QDesktopServices.openUrl) — мелочь,
сильно улучшающая ежедневный сценарий.

**Приёмка:** сохранение работает из смоука; путь в тосте короткий.

### F11. get_available_models: искать реальные имена файлов

`Transcriber.get_available_models()` перебирает `ggml-{tiny…turbo}.bin`,
а в каталоге лежат `ggml-large-v3-turbo-q8_0.bin` и т.п. → всегда `[]`.
Функция, судя по всему, мёртвая — **проверь вызовы**; если не используется,
удали; если используется (model_downloader?), сканируй `ggml-*.bin` глобом
и возвращай стемы без префикса.

**Приёмка:** юнит-тест с tmp-каталогом моделей: кладём
`ggml-large-v3-turbo-q8_0.bin` → он в списке.

### F12. Валидация «≥10 секунд между главами»

В `format_youtube_description` добавить фильтр: пункт, чей `start` ближе
10 с к предыдущему оставленному, пропускается (кроме первого). YouTube
молча отключает главы при нарушении. Требование «≥3 пунктов» **не**
навязывать форматтеру (он контент-агностичен), но если после фильтрации
осталось <3 — логировать warning.

**Приёмка:** юнит-тест: пункты на 0/5/12/21 с → остаются 0/12/21(→0:00 первым).

### F13. deleteLater для завершённых воркеров

В `youtube_panel` подключить `worker.finished.connect(worker.deleteLater)`
недостаточно (сигнал кастомный, эмитится до конца run) — правильнее
`QThread.finished` (базовый сигнал Qt) → `deleteLater`. Проверь, что
`BaseWorker` не перекрывает его. То же в `insights_panel`, если там нет.

**Приёмка:** смоук не падает; повторные генерации не растят список детей
панели (можно проверить `len(panel.findChildren(QThread))` в тесте смоука).

### F14. Удалить/изолировать legacy-пайплайн zoom_to_blog

`zoom_to_blog.py` (512 строк) и `run_workflow.sh` используют **openai-whisper
CLI** (`whisper` команда) — другой движок, чем всё приложение (whisper.cpp).
Это параллельная вселенная: свои промпты, свой вывод, свои зависимости,
никем не тестируется. `Description.md` его рекламирует.

**Что сделать:** переместить оба файла в `docs/archive/legacy/` (или удалить
— git помнит), убрать упоминание из `Description.md`. **Не** пытаться
портировать на whisper.cpp в рамках этого плана — будущий CLI строится на
`batch_processor`/`transcriber` (см. STRATEGY.md и UI-план).

**Приёмка:** `grep -r zoom_to_blog --include="*.py" --include="*.sh"
--include="*.md" .` — только в archive; тесты зелёные.

### F15. Ребрендинг остатков «Whisper Fedora»

Обновить докстринги-заголовки (`batch_processor.py`, `lm_studio_manager.py`,
`setup_diarization.py` и др. — `grep -rn "Whisper Fedora"`), имена иконок в
`build.py` не трогать (файлы реально так называются) — либо переименовать
файлы вместе со ссылками одним коммитом. Каталог `~/.whisper-fedora`
**оставить** — `core/paths.py` уже делает миграционный fallback, менять
рискованно и незачем.

### F16. Довести i18n в main_window и панелях

Захардкоженные строки: «Starting batch processing...» (`main_window.py:773`),
«Нет транскрипта для обработки» (`:1228`), «No transcription to clean»,
«No text to process», русские строки в `_on_book_finished`, тултип
диаризации «Identify different speakers…», тултип perf_combo. Вынести в
`locales/*.json` (структура ключей уже есть, en/ru полностью синхронны —
поддерживай это).

**Приёмка:** `grep -n '"[А-Яа-я]' ui/*.py` — пусто (кроме комментариев);
тест `test_i18n` дополнен проверкой новых ключей в обоих языках.

### F17. Мелочь одним коммитом

- `tests/test_lm_client.py::test_api_key_not_logged` — переписать: caplog
  на реальном вызове с моком urlopen, assert ключа нет ни в одном сообщении.
- `_TAB_KEYS` и `_edit_map` в `youtube_panel` — один источник:
  список кортежей `(key, edit_attr)`.
- Склейку «описание + тайм-коды» (`_maybe_compose_description`) вынести в
  чистую функцию `core/youtube_description.compose_full_description(desc,
  chapters)` + юнит-тест.

---

## 3. Порядок коммитов

1. `test: add unified Qt stubs in conftest.py, fix suite hang` (F1)
2. `fix: stop all panel workers in MainWindow.closeEvent` (F4)
3. `fix: resolve Auto language to transcript language for insights` (F5)
4. `fix: raise insight max_tokens and detect truncated responses` (F3)
5. `fix: sample transcript evenly instead of truncating tail` (F2)
6. `fix: disconnect stale youtube workers before new run` (F6)
7. `fix: surface generation errors per provider in youtube panel` (F7)
8. `fix: provider dialog no longer silently switches active provider` (F8)
9. `fix: mask all secret fields in config CLI output` (F9)
10. `fix: save youtube files to user data dir, friendly toast path` (F10)
11. `fix: get_available_models matches real model filenames` (F11)
12. `fix: enforce 10s minimum gap between youtube chapters` (F12)
13. `fix: deleteLater for finished insight workers` (F13)
14. `chore: archive legacy openai-whisper pipeline (zoom_to_blog)` (F14)
15. `chore: finish Whispered rebranding in module headers` (F15)
16. `fix: move hardcoded UI strings to locales` (F16)
17. `test/refactor: youtube panel cleanups` (F17)

Каждый коммит: `ruff` чистый, pytest зелёный **и завершается**, смоук ок.

---

## 4. Чего НЕ делать

- Не переписывать UI-компоновку — этим занимается отдельный план
  `docs/plans/UI_REDESIGN_PLAN.md`; здесь только фиксы поведения.
- Не менять форматы промптов и существующие типы инсайтов.
- Не делать облако дефолтом; не добавлять телеметрию.
- Не трогать `_run_transcription_process` и мультипроцессную отмену.
- Не пушить в удалённые ветки без отдельной просьбы.
