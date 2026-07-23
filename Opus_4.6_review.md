# Opus 4.6 Code Review — Whispered

> **Дата**: 2026-07-23  
> **Область**: Полный код-ревью проекта на предмет багов, рейс-кондишнов, логических ошибок и архитектурных проблем.  
> **Правило**: Код не менять. Все исправления оформлены как инструкции для агента.

---

## Критические баги (P0)

### 1. `export_article_html` — IndexError при обработке одноcимвольного или пустого абзаца

**Файл**: `article_generator.py`, строки 641–645  
**Суть**: Код обращается к `p[1]` без проверки длины строки. Однозначный `IndexError` на абзаце длиной в 1 символ (например, одиночная цифра или пунктуация).

```python
elif p[0].isdigit() and p[1] == '.':
```

**Как воспроизвести**: Сгенерировать статью, содержащую абзац, который начинается с одной цифры без точки после неё, или абзац из одного символа-цифры.

**Инструкция агенту**: В файле `article_generator.py`, строка 642, замени условие на `elif len(p) > 1 and p[0].isdigit() and p[1] == '.':`

---

### 2. `_convert_to_wav` — предсказуемое имя temp-файла (гонка + перезапись)

**Файл**: `transcriber.py`, строки 33–35  
**Суть**: Имя временного WAV-файла формируется детерминированно: `{base_name}_whisper_temp.wav` в `tempfile.gettempdir()`. Если два процесса одновременно транскрибируют файлы с одинаковым именем, один перезапишет WAV другого → повреждённый результат или крэш. Также temp-файл может остаться от предыдущего запуска и содержать чужие данные.

**Инструкция агенту**: Замени формирование `output_path` на вызов `tempfile.NamedTemporaryFile(suffix='.wav', prefix=f'{base_name}_', delete=False)`, чтобы каждый вызов получал уникальное имя. Верни `output_path = tmp.name`.

---

### 3. `Transcriber.transcribe()` возвращает `None` при ошибке модели, но type hint обещает `TranscriptionWorker`

**Файл**: `transcriber.py`, строки 622, 631  
**Суть**: Метод `transcribe()` может вернуть `None` (строки 622, 631 — `return None`), но сигнатура возвращает `TranscriptionWorker`. Вызывающий код (`BatchWorker._process_item`, строка 139) вызывает `self._transcriber.transcribe(...)` и не проверяет результат на `None` — далее `finished_event.wait()` зависнет навечно, потому что ни `on_finished`, ни `on_error` никогда не будут вызваны.

**Инструкция агенту**: В `batch_processor.py`, метод `_process_item`, после вызова `self._transcriber.transcribe(...)` добавь проверку: если вернулся `None`, установи `item.status = BatchStatus.ERROR`, `item.error = "Model download failed"`, эмитни `item_error` и вернись из метода, не дожидаясь `finished_event`.

---

### 4. `assemble_draft` блокирует GUI-поток

**Файл**: `ui/main_window.py`, строки 1406–1430  
**Суть**: `_assemble_draft()` вызывает `assemble_draft()` (ffmpeg с re-encode) синхронно из GUI-потока. Для длинного видео это замораживает интерфейс на минуты. `subprocess.run(..., timeout=3600)` позволяет блокировку до часа.

**Инструкция агенту**: Вынеси вызов `assemble_draft()` в `QThread` (или `BaseWorker`), аналогично тому как сделаны `AIProcessingWorker` и `TranscriptionWorker`. Подключи прогресс к `operation_bar`, а `_assemble_draft` в `main_window.py` пусть только запускает worker и обрабатывает сигналы `finished`/`error`.

---

## Важные баги (P1)

### 5. `generate_all_formats` — closure-ловушка в цикле

**Файл**: `article_generator.py`, строки 501–508  
**Суть**: Внутри цикла `for i, fmt in enumerate(formats)` определяется замыкание `format_progress`, которое захватывает `base_progress` по ссылке, а не по значению. К моменту вызова `format_progress` переменная `base_progress` уже может быть переопределена следующей итерацией.

```python
for i, fmt in enumerate(formats):
    base_progress = 30 + int(60 * i / total_formats)
    def format_progress(pct, msg):  # ← захватывает base_progress по ссылке
```

**Инструкция агенту**: Добавь дефолтный аргумент, чтобы зафиксировать значение: `def format_progress(pct, msg, _base=base_progress):` и используй `_base` вместо `base_progress` в теле.

---

### 6. `_group_words_into_segments` — повторный import `re` на каждый flush

**Файл**: `transcriber.py`, строка 133  
**Суть**: Внутри вложенной функции `_flush()` выполняется `import re as _re` при каждом вызове. Это не баг в прямом смысле (Python кэширует модули), но это анти-паттерн и замедление. Важнее то, что `re` уже импортирован на верхнем уровне файла (строка 7).

**Инструкция агенту**: Удали `import re as _re` из `_flush()` и замени `_re.sub(...)` на `re.sub(...)` (модуль `re` уже импортирован глобально).

---

### 7. `LMStudioClient.check_connection` не передаёт авторизационные заголовки

**Файл**: `core/lm_client.py`, строки 48–55  
**Суть**: `check_connection()` и `get_loaded_model()` не включают auth-заголовки (`_auth_headers()`). Если API требует авторизации (OpenAI-compatible провайдер с ключом), проверка соединения всегда вернёт `False`, хотя реальные запросы с ключом работают.

**Инструкция агенту**: В методах `check_connection` и `get_loaded_model` создавай `Request` с хедерами: `req = urllib.request.Request(url, headers=self._auth_headers())`.

---

### 8. `AnthropicClient` — `on_token` callback никогда не вызывается

**Файл**: `core/anthropic_client.py`, строка 43  
**Суть**: Docstring говорит «on_token is accepted for interface parity but never called». Это значит, что при использовании Anthropic-провайдера в YouTube/Insights панелях прогресс-бар не обновляется — пользователь видит зависший UI без индикации, что что-то происходит.

**Инструкция агенту**: Реализуй streaming через Anthropic Messages API (SSE с `"stream": true`), аналогично `LMStudioClient.chat_completion_stream`. Если стриминг слишком сложен — хотя бы вызывай `on_token(full_text)` одним куском после получения ответа, чтобы UI обновился.

---

### 9. `Recorder._audio_callback` — затенение переменной `exc`

**Файл**: `core/recorder.py`, строки 140–148  
**Суть**: Во вложенном `except Exception as exc:` (строка 145) имя `exc` затеняет внешний `except Exception as exc:` (строка 140). В Python 3, `exc` из внешнего блока будет удалена после завершения `except`-блока. Это не приводит к runtime-ошибке в данном случае, но маскирует исходную ошибку открытия потока.

```python
except Exception as exc:    # L140 — stream open failure
    ...
    except Exception as exc:  # L145 — wav close
```

**Инструкция агенту**: Переименуй внутренний `except Exception as exc:` в `except Exception as close_exc:` (строка 145).

---

### 10. `TextCleaner._quick_clean` — ложные удаления обычных слов

**Файл**: `text_processor.py`, строки 61–67 + 101–113  
**Суть**: `FILLER_PATTERNS` содержит `"like "`, `"so "`, `"well "`, `"right "` — обычные английские слова с пробелом. Замена через `str.replace()` не проверяет границы слов. Фраза «I would like to go» превратится в «I would to go». Аналогично «She played so well» → «She played  ».

**Инструкция агенту**: Либо используй regex с `\b` word boundaries для каждого паттерна в `_quick_clean`, либо примени удаление только к паттернам, которые точно являются филлерами (uh, um, uhm, er, ah, hmm), а для остальных (`like`, `so`, `well`, `right`) — только если они стоят в начале предложения или после запятой.

---

### 11. `data_dir()` на macOS — неожиданное поведение при наличии legacy-директории

**Файл**: `core/paths.py`, строки 39–46  
**Суть**: `_LEGACY_DIR = Path.home() / ".whisper-fedora"`. Если у macOS-пользователя случайно есть директория `~/.whisper-fedora` (например, скопирована с Linux-машины), все данные будут писаться туда вместо `~/Library/Application Support/Whispered`. Пользователь не увидит свою историю.

**Инструкция агенту**: Добавь проверку платформы: `if _LEGACY_DIR.exists() and platform.system() == "Linux":` вместо просто `if _LEGACY_DIR.exists():`. Или переименуй legacy_dir в что-то Linux-специфичное.

---

### 12. `chat_worker._build_system_prompt` — хардкод английского текста для truncation suffix

**Файл**: `core/chat_worker.py`, строки 27–31  
**Суть**: Промпт для чата с транскриптом всегда на английском, включая суффикс `"[Transcript truncated due to context limit]"`. Для русскоязычных пользователей это выглядит несогласованно и может сбить модель с русского на английский.

**Инструкция агенту**: Используй `tr()` для системного промпта и truncation-суффикса, или хотя бы сделай промпт двуязычным.

---

## Средние проблемы (P2)

### 13. `config.py` — API-ключи хранятся в plain-text JSON

**Файл**: `config.py`, строки 89–92  
**Суть**: `yt_openai_api_key` и `yt_anthropic_api_key` сериализуются через `json.dump(asdict(self), ...)` в файл с правами `0o600`. Хотя `chmod` ограничивает доступ, ключи хранятся в открытом виде. При этом в CLI-тесте (строка 177) есть маскировка, но она не защищает сам файл.

**Инструкция агенту**: Это осознанный trade-off (desktop app, owner-only permissions). Добавь предупреждение в README/документацию. Опционально — используй `keyring` библиотеку для OS-level secure storage на поддерживаемых платформах.

---

### 14. `HistoryStore._connect` — PRAGMA на каждом соединении

**Файл**: `core/history.py`, строки 190–203  
**Суть**: Каждый вызов `_connect()` выполняет `PRAGMA journal_mode=WAL` и `PRAGMA busy_timeout=5000`. WAL mode — persistent и устанавливается один раз на БД, а не на каждое соединение. Это лишние запросы на каждую операцию.

**Инструкция агенту**: Вынеси `PRAGMA journal_mode=WAL` в `_init_db()` (один раз). `busy_timeout` можно оставить на каждом соединении (он per-connection).

---

### 15. `_split_into_chunks` — дублирование логики разбивки

**Файл**: `article_generator.py:99`, `text_processor.py:215`, `book_pipeline.py:86`  
**Суть**: Три почти идентичные реализации chunking-функции в трёх разных файлах. Разные дефолтные параметры, незначительные отличия в сепараторах. Изменение логики (например, улучшение sentence boundary detection) нужно вносить трижды.

**Инструкция агенту**: Вынеси одну общую `split_into_chunks(text, chunk_size, overlap, separators)` в `core/llm_text.py` (рядом с `sample_lines_evenly`). Импортируй и используй из всех трёх модулей. Сохрани обратную совместимость через дефолтные параметры.

---

### 16. `LMStudioClient` — нет модели по умолчанию в payload

**Файл**: `core/lm_client.py`, строки 116–117 и 180–181  
**Суть**: Если `self._model` не задан (пустая строка — дефолт для LM Studio, где модель одна), поле `"model"` не включается в payload. Для OpenAI/cloud API это приведёт к ошибке «model is required».

**Инструкция агенту**: Для `provider_from_config` с `kind="openai"` — убедись, что `model` всегда заполнен (fallback на `cfg.yt_openai_model`). Для `kind="lmstudio"` — текущее поведение корректно (LM Studio использует загруженную модель).

---

### 17. Нет таймаутов для LM Studio connection check в `TextProcessor`

**Файл**: `text_processor.py`, строки 145, 294  
**Суть**: `self.lm_client.check_connection()` используется inline перед обработкой (строки 145, 294). Если LM Studio запущен, но завис, этот вызов блокирует GUI на 5 секунд (default timeout). Для `TextCleaner.clean()` это вызывается из `AIProcessingWorker` (в фоне), но для `CoherenceProcessor.process()` — тоже из worker'а. Проблема в том, что `ArticleGenerator.is_available()` (строка 325) вызывается из UI-потока через `AIProcessingPanel`.

**Инструкция агенту**: Убедись, что все вызовы `check_connection()` происходят из фоновых потоков, а не из UI-потока напрямую. В `AIProcessingPanel` — используй кэшированный статус вместо блокирующей проверки.

---

### 18. `_coalesce_batch_segments` — не обрабатывает пустой текст

**Файл**: `transcriber.py`, строка 178  
**Суть**: `" ".join(item.text.strip() for item in group if item.text.strip())` — корректно фильтрует пустые, но если все сегменты в группе имеют пустой текст, результирующий `Segment` будет с `text=""`. Затем `full_text` вернёт лишние пробелы.

**Инструкция агенту**: Добавь проверку: если после join текст пустой, не добавляй сегмент в `result`.

---

### 19. `export_pdf` — должен вызываться из main Qt thread, но нет проверки

**Файл**: `exporters.py`, строка 224  
**Суть**: Комментарий говорит «Must be called from the main Qt thread», но проверки нет. Если вызвать из `BatchProcessor.export_all()` (который работает в `QThread`), `QPrinter` крашнет приложение без внятной ошибки.

**Инструкция агенту**: Добавь проверку `QThread.currentThread() == QApplication.instance().thread()` в начало `export_pdf`. Если не в main thread — кидай `RuntimeError("PDF export must be called from the main Qt thread")`.

---

### 20. `_fts_query` — SQL injection через нестандартный FTS5 синтаксис

**Файл**: `core/history.py`, строки 108–119  
**Суть**: Функция экранирует двойные кавычки, но не экранирует другие FTS5-специальные символы: `*`, `^`, `NEAR`, `AND`, `OR`, `NOT`. Пользовательский ввод вроде `test OR drop` может сломать FTS-запрос или дать неожиданные результаты.

**Инструкция агенту**: Оберни каждый токен в двойные кавычки для literal matching: `f'"{t}"*'` (что уже делается). Дополнительно удаляй символы `*`, `^` из токенов перед формированием запроса, так как `"test*"*` — невалидный FTS5.

---

## Мелкие проблемы (P3)

### 21. `Recorder.start()` — timestamp в имени файла не учитывает таймзону

**Файл**: `core/recorder.py`, строка 110  
**Суть**: `datetime.now().strftime(...)` использует локальное время, но при сортировке файлов по имени результат зависит от таймзоны системы. Не баг, но неконсистентно с `HistoryStore.add()`, который использует `datetime.now(timezone.utc)`.

**Инструкция агенту**: Маловажно. Оставь как есть — имя файла для пользователя читаемее в локальном времени.

---

### 22. `SimpleDiarizer.diarize()` — всегда возвращает пустой результат

**Файл**: `diarizer.py`, строки 239–263  
**Суть**: `SimpleDiarizer` заявлен как fallback-диаризатор, но `diarize()` всегда возвращает пустой `DiarizationResult`. Это мёртвый код, который нигде не используется.

**Инструкция агенту**: Либо удали `SimpleDiarizer` класс, либо добавь TODO с пояснением, что это placeholder для будущей реализации на базе VAD (Voice Activity Detection).

---

### 23. `export_article_html` — не экранирует HTML в контенте

**Файл**: `article_generator.py`, строки 621–650  
**Суть**: `html_content = article.content` используется напрямую для regex-подстановок. Если LLM сгенерирует контент с `<script>` или `<iframe>`, они попадут в HTML-файл как есть. Для локального файла это не критично, но нарушает принцип least surprise.

**Инструкция агенту**: Добавь `import html` и вызывай `html.escape()` для текстового контента внутри `<p>`, `<li>` тегов. Не экранируй теги заголовков, которые сами генерируются regex'ами.

---

### 24. Нет graceful degradation при отсутствии `numpy` в `Recorder`

**Файл**: `core/recorder.py`, строки 29–34, 209–210  
**Суть**: Если `numpy` не установлен, `_NUMPY_AVAILABLE = False` и `level_changed` сигнал никогда не эмитится. Уровнемер в UI будет всегда на нуле. Запись работает, но пользователь не видит визуального фидбека.

**Инструкция агенту**: Если numpy недоступен, реализуй fallback-расчёт RMS через `struct.unpack` и чистый Python. Или хотя бы эмить фиксированное значение (например, 0.5) во время записи, чтобы пользователь видел, что запись идёт.

---

### 25. `config.py` CLI — `len(value)` может крашнуть на не-строковых полях

**Файл**: `config.py`, строки 176–177  
**Суть**: `if value and ('token' in key.lower() or 'key' in key.lower()): value = value[:8] + "..." if len(value) > 8 else "***"` — если `value` — число (int/float), `len(value)` кинет `TypeError`.

**Инструкция агенту**: Добавь `isinstance(value, str)` в условие: `if isinstance(value, str) and value and ('token' in key.lower() or 'key' in key.lower()):`

---

### 26. `book_pipeline.py` — бесконечный цикл в `_versioned_path` при невозможности записи

**Файл**: `book_pipeline.py`, строки 67–83  
**Суть**: `while True: ... n += 1` — если директория read-only и ни один файл не может быть создан, `path.exists()` всегда вернёт `False` для нового кандидата, и функция выйдет. Но если файловая система создаёт файлы при `exists()` check (маловероятно), цикл бесконечен. Теоретически безопасно, но стоит добавить guard.

**Инструкция агенту**: Добавь `if n > 1000: raise RuntimeError("Too many versions")` в цикл.

---

### 27. `lm_studio_manager.py` — не используется нигде в основном приложении

**Файл**: `lm_studio_manager.py`  
**Суть**: 448 строк кода для управления LM Studio через CLI. Не импортируется ни одним модулем в проекте (проверено по imports). Мёртвый код.

**Инструкция агенту**: Если модуль планируется к использованию — добавь TODO. Если нет — удали или перемести в `tools/` директорию.

---

## Архитектурные замечания (не баги)

### A1. `main_window.py` — God Object (1501 строка)

`MainWindow` отвечает за транскрипцию, AI-обработку, batch, book pipeline, video editing, YouTube generation, live transcription, историю, экспорт, drag-and-drop, settings, shortcuts. Все связи между компонентами проходят через него.

**Рекомендация**: Выноси бизнес-логику в контроллеры: `TranscriptionController`, `AIProcessingController`, `PresetChainController`. MainWindow должен только маршрутизировать сигналы.

### A2. Глобальные синглтоны через `get_config()` / `get_history_store()`

Config и HistoryStore — ленивые глобалы. Затрудняет тестирование и подмену. Лучше передавать через DI.

### A3. Прямой доступ к приватным атрибутам

В нескольких местах UI-код напрямую обращается к приватным атрибутам:
- `self.live_view._timer.start()` (main_window.py:496)
- `self.youtube_panel._cancel_workers(...)` (main_window.py:830)
- `self.file_selector._set_file(...)` (main_window.py:691, 738)
- `self.book_panel._batch_worker` (main_window.py:187)
- `self.recorder_widget._toggle_recording` (main_window.py:565)

**Рекомендация**: Создай публичные методы-обёртки.

---

## Чек-лист приоритетов для агента

| #  | Приоритет | Кратко                                           | Файл(ы)                      |
|----|-----------|--------------------------------------------------|-------------------------------|
| 1  | P0        | IndexError в export_article_html                 | article_generator.py:642      |
| 2  | P0        | Race condition в _convert_to_wav temp file       | transcriber.py:33-35          |
| 3  | P0        | BatchWorker зависает если transcribe() → None    | batch_processor.py:139-154    |
| 4  | P0        | assemble_draft блокирует GUI                     | ui/main_window.py:1406-1430   |
| 5  | P1        | Closure-ловушка в generate_all_formats           | article_generator.py:501-508  |
| 6  | P1        | Повторный import re в _flush()                   | transcriber.py:133            |
| 7  | P1        | check_connection без auth headers                | core/lm_client.py:48-55      |
| 8  | P1        | Anthropic on_token не вызывается                 | core/anthropic_client.py:43   |
| 9  | P1        | Затенение exc в Recorder.start()                 | core/recorder.py:140-148      |
| 10 | P1        | Ложные удаления слов в _quick_clean              | text_processor.py:61-113      |
| 11 | P1        | Legacy dir на macOS                              | core/paths.py:39-46           |
| 12 | P1        | Хардкод английского в chat system prompt         | core/chat_worker.py:19-31     |
| 13 | P2        | API ключи в plaintext                            | config.py:89-92               |
| 14 | P2        | PRAGMA WAL на каждом соединении                  | core/history.py:190-203       |
| 15 | P2        | Дублирование chunking логики                     | 3 файла                       |
| 16 | P2        | Нет model fallback для OpenAI provider           | core/lm_client.py:116-117     |
| 17 | P2        | check_connection из UI-потока                    | text_processor.py             |
| 18 | P2        | Пустой текст в coalesced segments                | transcriber.py:178            |
| 19 | P2        | export_pdf без thread check                      | exporters.py:224              |
| 20 | P2        | FTS5 спецсимволы не экранируются                 | core/history.py:108-119       |
| 21 | P3        | Timestamp в имени записи                         | core/recorder.py:110          |
| 22 | P3        | SimpleDiarizer — мёртвый код                     | diarizer.py:225-263           |
| 23 | P3        | HTML не экранируется в export_article_html        | article_generator.py:621-650  |
| 24 | P3        | Нет fallback RMS без numpy                       | core/recorder.py:29-34        |
| 25 | P3        | len() на не-строке в config CLI                  | config.py:176-177             |
| 26 | P3        | Нет guard в _versioned_path                      | book_pipeline.py:67-83        |
| 27 | P3        | lm_studio_manager не используется                | lm_studio_manager.py          |
