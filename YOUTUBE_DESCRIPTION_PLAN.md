# Инструкция для Sonnet: генератор YouTube-описания с тайм-кодами

> **Контекст.** Whispered — офлайн-приложение транскрипции на PyQt6 (whisper.cpp +
> локальный LLM через LM Studio). Нужно добавить фичу: по готовому транскрипту
> сгенерировать готовый к вставке под ролик на YouTube блок **тайм-кодов по
> ключевым темам**, показать его в отдельной вкладке с кнопкой «Копировать».
> Код базы качественный и покрыт тестами — действуй в том же стиле.

## Решения заказчика (фиксированы, не переспрашивать)

1. **Содержание вывода:** ТОЛЬКО тайм-коды по темам. Без вводного абзаца, без
   хэштегов, без списка тезисов. Чистый список вида `0:00 Тема`.
2. **Где показывать:** **новая вкладка** в основном интерфейсе (рядом с
   Insights/Chat), результат остаётся открытым; не модальное окно.
3. **Связь с существующей фичей:** **переиспользовать генерацию «Глав»**
   (`InsightsWorker` с типом `chapters`) как источник тайм-кодов. Не писать
   второй промпт распознавания тем с нуля.
4. **Язык вывода:** **выбор языка перед генерацией** (комбобокс во вкладке):
   `Авто (как транскрипт)`, `Русский`, `English`. «Авто» = не навязывать язык.

## Правила работы (обязательно)

- **Ветка:** разрабатывай в `claude/friendly-tesla-vxnm75` (это ветка PR #2).
  Не пушь в другие ветки. **PR не создавать.**
- **По одному логическому изменению — один коммит** с понятным сообщением.
- После каждого изменения: `python -m pytest tests/ -q` (должно быть зелёным)
  и `python -m compileall -q core ui` по затронутым модулям.
- **Новый чистый код (без Qt) покрывай юнит-тестами.**
- Не ломай публичные сигнатуры, на которые есть вызовы: сначала добавь
  опциональный параметр со значением по умолчанию, потом используй.
- Локализуй все строки UI через `tr(...)`, добавляя ключи в `locales/en.json`
  и `locales/ru.json` (структура — плоский словарь ключ→строка).
- CI уже использует `compileall` (не ручной список) — новые файлы подхватятся
  автоматически, править `.github/workflows/ci.yml` не нужно.

---

## Архитектура

Три новых файла + правки в `main_window.py` и локалях.

### 1. `core/youtube_description.py` — чистая логика форматирования (без Qt)

Две функции, обе тестируемые:

```
format_youtube_timestamp(seconds: int) -> str
format_youtube_description(chapters: list[dict]) -> str
```

**`format_youtube_timestamp(seconds)`** — формат, который понимает YouTube:
- при `seconds < 3600` → `M:SS` (минуты БЕЗ ведущего нуля, секунды всегда 2
  цифры): `0`→`0:00`, `154`→`2:34`, `725`→`12:05`.
- при `seconds >= 3600` → `H:MM:SS`: `3723`→`1:02:03`.
- Не переиспользуй `utils.format_duration` — там минуты zero-padded (`02:34`),
  а YouTube для глав ожидает `2:34`. Это отдельный формат.

**`format_youtube_description(chapters)`** — превращает список глав в готовый
текст. Вход: `list[{"start": int, "title": str}]` (то, что отдаёт
`InsightsWorker` для типа `chapters`). Алгоритм:
1. Отбросить элементы с пустым/пробельным `title`.
2. Привести `start` к int (защититься от float/строк; некорректные — пропустить).
3. Отсортировать по `start` по возрастанию.
4. Оставить строго возрастающие по времени (если `start <=` времени предыдущей
   оставленной — пропустить дубль/инверсию).
5. **Принудительно** сделать `start` первого элемента равным `0` (требование
   YouTube: первая глава обязана быть `0:00`).
6. Сформировать строки `f"{format_youtube_timestamp(start)} {title}"`,
   склеить через `\n`.
7. Если валидных элементов нет — вернуть `""`.

> Требования YouTube к главам (≥3 главы, шаг ≥10 c) адресуются в основном
> промптом (см. ниже). Форматтер гарантирует только `0:00` в начале и
> возрастание — этого достаточно, чтобы YouTube распознал главы.

### 2. Расширить `core/insights_worker.py` — опциональный язык вывода

Минимальное обратносовместимое изменение, чтобы переиспользовать главы, но
управлять языком заголовков:

- В `InsightsWorker.__init__(...)` добавить параметр `language: Optional[str] = None`
  (после `lm_url`), сохранить в `self._language`.
- В `_build_prompt_text(insight_type, segments, max_transcript_chars=..., language=None)`
  добавить параметр `language`. Если `language` задан — вставить **после**
  системного промпта (`load_prompt(...)`) и **до** транскрипта одну строку-директиву,
  например: `f"Write all chapter titles in {language}.\n"`. Если `None` —
  поведение не меняется (текущая вкладка Insights продолжает работать как есть).
- В `_execute()` пробросить `self._language` в `_build_prompt_text(...)`.
- Вкладка Insights создаёт воркер без `language` → `None` → старое поведение
  сохранено. Не трогать `ui/insights_panel.py`.

### 3. `ui/youtube_panel.py` — вкладка (шаблон — `ui/insights_panel.py`)

Скопируй жизненный цикл и стиль из `ui/insights_panel.py` (он уже делает ровно
то же: держит сегменты, гоняет `InsightsWorker`, корректно отменяет в `clear()`).

Класс `YouTubePanel(QWidget)`. Виджеты:
- Комбобокс языка (`QComboBox`): пункты `youtube_lang_auto` (data=`None`),
  `Русский` (data=`"Russian"`), `English` (data=`"English"`).
- Кнопка `youtube_generate` (`variant="primary"`), `setEnabled(False)` пока нет
  сегментов.
- Read-only многострочное поле `QPlainTextEdit` (моноширинный шрифт) для вывода.
- Кнопка `youtube_copy` («Копировать»), `setEnabled(False)` пока пусто;
  по клику — `QApplication.clipboard().setText(...)` + тост `show_toast(...)`.
- Лейбл-плейсхолдер `youtube_placeholder` (как в insights_panel).

Публичный API (как у `InsightsPanel`):
- `set_segments(segments)` — сохранить сегменты, включить «Сгенерировать»,
  скрыть плейсхолдер.
- `clear()` — отменить активный воркер (паттерн из `InsightsPanel.clear()`:
  `cancel()`, отключить сигналы, `wait(2000)`), очистить поле и сегменты.

Логика генерации:
1. По кнопке: взять `lm_url = get_config().lm_studio_url`; если пусто — показать
   `youtube_no_lm` и выйти.
2. Заблокировать кнопку, текст → `youtube_generating`.
3. `lang = self._lang_combo.currentData()`.
4. `w = InsightsWorker("chapters", self._segments, lm_url, language=lang, parent=self)`.
5. Подключить `w.finished` и `w.error_occurred`, `w.start()`.
6. В `_on_finished(insight_type, data)`: если `isinstance(data, list)` →
   `text = format_youtube_description(data)`; если `text` пуст → показать
   `youtube_empty`; иначе вставить `text` в поле, включить «Копировать».
   Если `data` не список (фолбэк-сырой текст из воркера) — показать `youtube_error`.
7. В `_on_error(insight_type, msg)`: показать `youtube_error` + `msg`.
8. В обоих случаях разблокировать кнопку, вернуть текст `youtube_generate`.

> `InsightsWorker.finished` имеет сигнатуру `(str, object)`, `error_occurred` —
> `(str, str)`. Первый аргумент — тип инсайта (`"chapters"`), используй/игнорируй.

### 4. Интеграция в `ui/main_window.py` (точные якоря)

- Импорт рядом со строкой 29 (`from ui.insights_panel import InsightsPanel`):
  `from ui.youtube_panel import YouTubePanel`.
- Создание вкладки — после блока Insights (строки 355–357), перед вкладкой
  History (строки 359–361), чтобы History осталась последней:
  ```
  self.youtube_panel = YouTubePanel()
  self.content_tabs.addTab(self.youtube_panel, tr("tab_youtube"))
  ```
- В `_on_finished(...)` рядом со строкой 805 (`self.insights_panel.set_segments(result.segments)`)
  добавить `self.youtube_panel.set_segments(result.segments)`.
- В `_load_from_history(...)` рядом со строкой 872 — то же:
  `self.youtube_panel.set_segments(result.segments)`.
- В `_start_transcription(...)` рядом со строкой 675 (`self.insights_panel.clear()`)
  добавить `self.youtube_panel.clear()`.
- Вкладке YouTube не нужен `seek_requested` (вывод — это текст, не кликабельные
  строки). Если захочешь сделать тайм-коды кликабельными — это отдельная задача,
  в текущий объём не входит.

### 5. Локали — добавить ключи в `locales/en.json` и `locales/ru.json`

| ключ | en | ru |
|---|---|---|
| `tab_youtube` | `YouTube` | `YouTube` |
| `youtube_placeholder` | `Transcribe a recording first to generate a YouTube description.` | `Сначала транскрибируйте запись, чтобы сгенерировать описание для YouTube.` |
| `youtube_generate` | `Generate description` | `Сгенерировать описание` |
| `youtube_generating` | `Generating…` | `Генерирую…` |
| `youtube_copy` | `Copy` | `Копировать` |
| `youtube_copied` | `Copied to clipboard` | `Скопировано в буфер обмена` |
| `youtube_lang_auto` | `Auto (transcript language)` | `Авто (язык транскрипта)` |
| `youtube_empty` | `No chapters were detected.` | `Не удалось выделить темы.` |
| `youtube_error` | `Error:` | `Ошибка:` |
| `youtube_no_lm` | `Configure LM Studio URL in Settings first.` | `Сначала укажите адрес LM Studio в настройках.` |

> Помни про порядок JSON и валидность (последний ключ без хвостовой запятой).

### 6. Тесты — `tests/test_youtube_description.py`

Только чистая логика (`core/youtube_description.py`). Так как импорт
`core.youtube_description` тянет `core/__init__.py` (а он импортирует Qt-модули),
**скопируй заголовок-заглушку Qt из `tests/test_llm_text.py`** (блок
`sys.modules.setdefault(...)` для `PyQt6.*`, `core.lm_client`, `core.ai_worker`)
в начало файла, потом импортируй тестируемые функции.

Покрой как минимум:
- `format_youtube_timestamp`: `0→"0:00"`, `154→"2:34"`, `725→"12:05"`,
  `3723→"1:02:03"`, `3600→"1:00:00"`.
- `format_youtube_description`:
  - первый элемент принудительно становится `0:00` (вход со `start>0`);
  - сортировка по времени для неотсортированного входа;
  - пропуск элементов с пустым `title`;
  - пропуск инверсий/дублей по времени;
  - пустой вход / список без валидных элементов → `""`;
  - корректная склейка строк через `\n`.

---

## Порядок коммитов

1. `core/youtube_description.py` + `tests/test_youtube_description.py`
   (чистая логика и тесты — самостоятельный, легко проверяемый шаг).
2. Расширение `core/insights_worker.py` параметром `language` (+ при желании
   тест на то, что директива языка попадает в промпт, по образцу
   `tests/test_llm_text.py::test_build_prompt_text_*`).
3. `ui/youtube_panel.py` + ключи локалей.
4. Интеграция в `ui/main_window.py` (вкладка + три вызова `set_segments`/`clear`).
5. Прогон полного `pytest`, `compileall`, пуш в `claude/friendly-tesla-vxnm75`.

## Критерии приёмки

- Появилась вкладка «YouTube»; кнопка «Сгенерировать» неактивна, пока нет
  транскрипта, и активируется после транскрипции и при загрузке из истории.
- Выбор языка влияет на язык заголовков тайм-кодов.
- Результат — корректный список `0:00 …` (первая строка ровно `0:00`),
  по возрастанию времени; кнопка «Копировать» кладёт его в буфер.
- При незаданном LM Studio — понятное сообщение, без падения.
- `pytest` зелёный (включая новые тесты), `compileall` без ошибок.

## Чего НЕ делать

- Не добавлять вводный абзац, хэштеги, тезисы (заказчик выбрал только тайм-коды).
- Не переписывать `ui/insights_panel.py` и существующий промпт `prompts/chapters.md`.
- Не делать модальное окно — только вкладка.
- Не создавать PR и не пушить в другие ветки.
- Не плодить второй клиент LM Studio — переиспользуй `InsightsWorker`
  (он внутри использует `LMStudioClient` и общий каркас `BaseWorker`).
