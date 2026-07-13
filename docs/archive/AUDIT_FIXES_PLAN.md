# План для Sonnet: исправление багов по итогам аудита YouTube-функционала

> **Контекст.** Whispered — офлайн-приложение транскрипции на PyQt6 (whisper.cpp +
> локальный LLM через LM Studio; опциональное облако только для YouTube-вкладки).
> Проведён аудит недавно добавленного функционала (провайдеры ИИ, тайм-коды,
> описание, сохранение в `output/`) и смежного кода. Ниже — подтверждённые баги
> (каждый проверен чтением кода и/или реальным прогоном) и задачи по исправлению.
> Документ самодостаточен: читай целиком до начала работы.
> Стиль кода и правила — как в `ROADMAP.md` §1 и `docs/plans/YOUTUBE_KEY_QUESTIONS_PLAN.md`.

---

## 0. Сводка подтверждённых багов

| № | Серьёзность | Где | Суть |
|---|---|---|---|
| B1 | **Высокая** | `core/insights_worker.py:26`, `core/llm_text.py` | Транскрипт обрезается по хвосту на 48 000 символов → тайм-коды не покрывают конец видео. **Измерено на реальном файле:** видео 48:41, транскрипт с префиксами `[Ns]` = 56 601 символ, обрезка пришлась на 41:28 — модель не видела последние 7 минут. Для 2-часовых записей потеряется больше половины. |
| B2 | **Высокая** | `ui/youtube_panel.py:_generate`, `ui/main_window.py` | Язык «Авто» передаёт `language=None` → в промпт не попадает языковая директива → модель отвечает на английском. **Воспроизведено:** русский транскрипт, Gemma-4-12b, главы вышли на английском ("Introduction to systematization…"). |
| B3 | **Высокая** | `ui/main_window.py:75` (`closeEvent`) | Закрытие окна во время генерации YouTube/Insights не останавливает воркеры → Qt падает с "QThread: Destroyed while thread is still running". Нарушение ROADMAP §1.2 п.3 («каждая длительная операция корректно завершается в closeEvent»). |
| B4 | Средняя | `ui/youtube_panel.py:_generate`, `:clear` | Сигналы старых воркеров не отсоединяются перед сбросом ссылок. Если `wait(1000)` истёк, «протухший» воркер позже эмитит `finished`/`error_occurred` в новый прогон: ломает счётчик `_pending`, перезаписывает вкладки. В `ui/insights_panel.py` (~строки 210–218) эта защита **уже есть** — используй её как образец. |
| B5 | Средняя | `ui/youtube_panel.py:_on_error`, `core/insights_worker.py:_execute` | Ошибки генерации молча проглатываются: `_on_error` только логирует, вкладки остаются пустыми. Текст ошибки всегда «LM Studio did not respond» — даже когда провайдер Anthropic/OpenAI. Если последний завершившийся воркер упал с ошибкой, кнопки «Копировать»/«Сохранить» остаются выключенными, хотя остальные вкладки заполнены. |
| B6 | Средняя | `ui/provider_dialog.py:_on_accept` | Диалог настройки пишет `cfg.yt_provider` из своего комбо, а комбобокс панели не обновляется → UI показывает один провайдер, генерация идёт через другой. Пользователь, переключивший комбо в диалоге «просто посмотреть поля», незаметно меняет активного провайдера. |
| B7 | Средняя (безопасность) | `config.py:162` (`__main__`-блок) | `python config.py` маскирует только `hf_token`; новые `yt_openai_api_key` / `yt_anthropic_api_key` печатаются открытым текстом. Нарушение правила «никогда не логировать ключи». |
| B8 | Средняя | `ui/youtube_panel.py:28` | `_OUTPUT_DIR = Path(__file__)…/output` — в PyInstaller-сборке это путь внутрь read-only бандла; сохранение сломается. |
| B9 | Низкая | `core/youtube_description.py:format_youtube_description` | Не проверяется требование YouTube «≥10 секунд между главами» — при более близких пунктах YouTube молча отключает главы у ролика. |
| B10 | Низкая | `ui/youtube_panel.py:_generate` | Каждое нажатие «Сгенерировать» создаёт 5 QThread с `parent=self`; завершённые никогда не удаляются (`deleteLater` нет) → тихое накопление объектов за сессию. |
| B11 | Низкая | разное | (а) `tests/test_lm_client.py::test_api_key_not_logged` ничего осмысленного не проверяет; (б) дублирование карт вкладок `_TAB_KEYS` и `_edit_map`; (в) тост `youtube_saved` показывает длинный абсолютный путь; (г) склейка «описание + тайм-коды» зашита в приватный метод панели и не покрыта тестами. |

---

## 1. Обязательные правила

1. **Офлайн — дефолт.** Ничего не менять в поведении по умолчанию (`yt_provider="lmstudio"`); облако — только явный выбор пользователя.
2. **Тесты без сети.** Мокать `urllib`/клиентов; паттерн стабов Qt — как в `tests/test_youtube_description.py` и `tests/test_insights_worker_provider.py`.
3. **Не трогать** пайплайн транскрипции (`transcriber.py`), `prompts/*.md`, `ui/insights_panel.py` (кроме вызова его `clear()` из `closeEvent` в B3), существующие типы инсайтов.
4. **Не ломать вызывающих.** `format_youtube_description`, `_build_prompt_text`, `InsightsWorker.__init__` — новые параметры только опциональные с безопасными дефолтами.
5. **Никогда не логировать ключи** — ни целиком, ни частично, ни в сообщениях об ошибках.
6. **Один шаг — один коммит.** Формат: `fix:` / `refactor:` / `test:`.
7. **Проверка перед каждым коммитом:** `ruff check .` (0 ошибок), `python -m pytest tests/ -q` (зелёный), `python -m compileall` изменённых файлов, headless-смоук:
   `QT_QPA_PLATFORM=offscreen .venv/bin/python -c "...создать MainWindow..."` — окно создаётся без ошибок (PyQt6 стоит в `.venv`, системный python без Qt — им гоняются юнит-тесты).
8. Папки `input/` и `output/` в `.gitignore` — не коммитить их содержимое.

---

## 2. Задачи (в порядке реализации)

### 2.1. B1 — равномерное прореживание транскрипта вместо обрезки хвоста

Сейчас `_build_prompt_text` (`core/insights_worker.py:58`) собирает строки
`[Ns] текст` и рубит результат `fit_to_context(text, 48_000)` — хвост исчезает.
Для инсайтов с тайм-кодами это фатально: конец видео не существует для модели.

Реализация:
- В `core/llm_text.py` добавить функцию:
  ```python
  def sample_lines_to_budget(lines: list[str], max_chars: int) -> list[str]:
      """Равномерно проредить список строк, чтобы суммарная длина
      (с \n) уложилась в max_chars. Первая и последняя строки сохраняются
      всегда. Если и так влезает — вернуть без изменений."""
  ```
  Простая стратегия: посчитать текущую длину; если превышает — оставлять каждую
  k-ю строку (k = ceil(текущая/бюджет)), затем при необходимости дорезать хвостом.
  Без изощрений: главное — равномерность покрытия и сохранение первой/последней строки.
- В `_build_prompt_text`: для **всех** типов инсайтов заменить
  `fit_to_context("\n".join(lines), max)` на `"\n".join(sample_lines_to_budget(lines, max))`.
  Равномерное покрытие полезно и для titles/description/tags (сейчас они тоже
  не видят конца разговора). Маркер об усечении можно не добавлять.
- `_TRANSCRIPT_MAX_CHARS` оставить 48 000.

**Приёмка:** юнит-тесты `tests/test_llm_text.py` (дополнить существующий файл):
короткий список возвращается как есть; длинный — укладывается в бюджет; первая и
последняя строки на месте; выборка равномерна (пример: 1000 строк по 100 симв.,
бюджет 10 000 → выжившие индексы распределены по всему диапазону, а не только
началу). Существующие тесты `fit_to_context` не трогать — функция остаётся для
других вызывающих (`chat_worker` и т.п.).

Коммит: `fix: sample transcript evenly instead of truncating tail for insights`.

### 2.2. B2 — язык транскрипта как дефолт для генерации

- В `ui/youtube_panel.py` добавить `set_language(self, code: str) -> None` —
  сохранить ISO-код (например `"ru"`) в `self._transcript_lang`; сбрасывать в `clear()`.
- Словарь-мэппинг ISO → английское имя языка (модели ожидают "Russian", не "ru").
  Положить в `core/llm_text.py` или новый маленький helper в `core/`:
  `language_name(code: str) -> str | None` — покрыть основные языки Whisper
  (ru, en, uk, de, fr, es, it, pt, pl, ja, zh, ko, tr, ar, nl, cs — достаточно),
  для неизвестного кода вернуть `None`.
- В `_generate()`: `lang = self._lang_combo.currentData() or language_name(self._transcript_lang)`.
  Если оба пусты — как раньше (без директивы).
- В `ui/main_window.py`:
  - после транскрипции (рядом с `youtube_panel.set_segments`, ~строка 843):
    `self.youtube_panel.set_language(result.language)`;
  - в `_load_from_history`: `self.youtube_panel.set_language(payload.get("language", ""))`.

**Приёмка:** юнит-тест на `language_name` (известные/неизвестный код). Ручная
проверка: русский транскрипт + комбо «Авто» → в собранный промпт попадает
`Write all output in Russian.` (проверяемо юнит-тестом `_build_prompt_text` c
`language="Russian"` — уже работает; ключевое — прокладка кода до панели).

Коммит: `fix: default generation language to detected transcript language`.

### 2.3. B4 — отсоединение сигналов старых воркеров (до B3, т.к. B3 опирается на `clear()`)

В `ui/youtube_panel.py` в **обоих** местах, где сбрасываются воркеры
(`_generate()` и `clear()`), перед `cancel()/wait()` отсоединить сигналы —
скопировать паттерн из `ui/insights_panel.py` (~строки 210–218):

```python
for w in self._workers.values():
    if w:
        try:
            w.finished.disconnect(self._on_finished)
            w.error_occurred.disconnect(self._on_error)
        except (RuntimeError, TypeError):
            pass
        if w.isRunning():
            w.cancel()
            w.wait(1000)
```

**Приёмка:** существующие тесты зелёные; смоук-тест: два подряд вызова
`_generate()` с мок-воркерами не приводят к двойному декременту `_pending`
(можно покрыть headless-тестом с фейковым воркером, эмитящим после сброса).

Коммит: `fix: disconnect stale worker signals in youtube panel`.

### 2.4. B3 — остановка воркеров в closeEvent

В `ui/main_window.py:closeEvent` (строка ~75), перед `event.accept()`:

```python
# Stop insight/youtube generation workers (QThread must not be destroyed running)
if hasattr(self, "youtube_panel"):
    self.youtube_panel.clear()
if hasattr(self, "insights_panel"):
    self.insights_panel.clear()
```

Проверь также `chat_panel`: если у него есть работающий воркер и метод
остановки — вызвать по аналогии; если метода нет, добавить **только** остановку
(не рефакторить панель).

**Приёмка:** headless-смоук: создать MainWindow, запустить генерацию с мок-путём
(или просто вызвать `close()` сразу) — процесс завершается без
"QThread: Destroyed while thread is still running" в stderr.

Коммит: `fix: stop insight and youtube workers on window close`.

### 2.5. B5 — показывать ошибки генерации пользователю

1. `core/insights_worker.py:_execute` — сообщение об ошибке сделать
   провайдер-нейтральным/точным: если `self._provider` задан — 
   `f"AI provider did not respond ({self._provider.kind})."`, иначе прежний текст
   про LM Studio. Ключ API в сообщение попадать не должен.
2. `ui/youtube_panel.py:_on_error` — записать текст ошибки в соответствующую
   вкладку: карта `insight_type → edit` уже есть (`_edit_map` — см. также 2.10,
   где карта консолидируется); текст: `tr("youtube_error") + " " + msg`.
3. Завершение генерации выделить в helper `_finalize_generation()`:
   вызывается из `_on_finished` и `_on_error`, когда `_pending == 0`;
   включает «Копировать»/«Сохранить», если **хоть одна** вкладка непуста.

**Приёмка:** headless-тест: вызвать `_on_error("chapters", "boom")` при
`_pending=1` → вкладка Chapters содержит "boom", кнопка Copy включена, если
другая вкладка была заполнена.

Коммит: `fix: surface generation errors in youtube tabs and finalize buttons`.

### 2.6. B6 — панель единолично владеет выбором провайдера

- В `ui/provider_dialog.py:_on_accept` **удалить** строку записи
  `cfg.yt_provider = ...`. Диалог сохраняет только URL/ключи/модели.
- В `ui/youtube_panel.py:_open_provider_dialog` после `dialog.exec()` вызвать
  `self._init_provider_from_config()` — на случай будущих изменений конфига.
- (Опционально, минор) placeholder'ы полей диалога показывать текущие дефолты
  (`https://api.openai.com/v1`, имена моделей), чтобы пустое поле было
  информативным.

**Приёмка:** headless-тест: панель на "openai" → открыть диалог, переключить
комбо диалога на "anthropic", OK → `cfg.yt_provider` остался `"openai"`, комбо
панели не изменилось; ключи Anthropic при этом сохранились.

Коммит: `fix: provider dialog no longer overrides panel provider selection`.

### 2.7. B7 — маскировать все секреты в CLI-выводе config.py

В `config.py`, `__main__`-блок: заменить проверку `key == 'hf_token'` на
общую — маскировать значение, если имя поля содержит `token` или `api_key`
(регистронезависимо) и значение непусто. Вынести в маленький helper
`_mask_secret(value: str) -> str` (первые 4 символа + `"…"`, либо `"***"` для
коротких), чтобы покрыть тестом.

**Приёмка:** юнит-тест в `tests/test_config.py`: `_mask_secret` не возвращает
исходное значение; поля `yt_openai_api_key`/`yt_anthropic_api_key`/`hf_token`
попадают под маску по имени.

Коммит: `fix: mask all secret fields in config CLI output`.

### 2.8. B8 — корректный output/ для замороженной сборки

- В `core/paths.py` добавить:
  ```python
  def output_dir() -> Path:
      """Куда сохранять сгенерированные файлы. Из исходников — <project>/output
      (git-ignored). В замороженной сборке (PyInstaller) — data_dir()/output."""
      if getattr(sys, "frozen", False):
          path = data_dir() / "output"
      else:
          path = Path(__file__).resolve().parent.parent / "output"
      path.mkdir(parents=True, exist_ok=True)
      return path
  ```
- В `ui/youtube_panel.py` удалить модульную константу `_OUTPUT_DIR`; в
  `_save_to_file` использовать `from core.paths import output_dir`.

**Приёмка:** юнит-тест: с `monkeypatch.setattr(sys, "frozen", True, raising=False)`
и подменённым `data_dir` путь уходит в data-директорию; без `frozen` — в корень
проекта. Существующее сохранение из UI работает как раньше.

Коммит: `fix: resolve output dir via core.paths for frozen builds`.

### 2.9. B9 — фильтр минимального интервала 10 с в форматтере

В `core/youtube_description.py:format_youtube_description` добавить параметр
`min_gap_seconds: int = 10`: при отборе `kept` пропускать элементы, чей `start`
ближе `min_gap_seconds` к предыдущему оставленному (сейчас условие
`start > kept[-1][0]` — заменить на `start >= kept[-1][0] + min_gap_seconds`).
Первый элемент — как раньше (принудительно `0`).

Существующие тесты используют интервалы ≥30 с либо дубликаты (gap=0) — они
останутся зелёными; проверь и при необходимости поправь только тесты с
интервалом 1–9 с (таких сейчас нет).

**Приёмка:** новые тесты: элементы с интервалом < 10 с отбрасываются; параметр
`min_gap_seconds=0` возвращает старое поведение; ≥10 с — сохраняются.

Коммит: `fix: enforce YouTube 10-second minimum chapter gap in formatter`.

### 2.10. B10 + B11 — очистка ресурсов и мелкий рефакторинг

1. **B10:** в `ui/youtube_panel.py` после отсоединения сигналов и `wait()`
   (см. 2.3) вызывать `w.deleteLater()` для отработанных воркеров — и в
   `_generate()`, и в `clear()`.
2. **B11г:** вынести склейку «описание + метка + тайм-коды» в чистую функцию
   `core/youtube_description.py`:
   ```python
   def compose_full_description(description: str, chapters: list[dict],
                                timecodes_label: str) -> str:
       """description + '\n\n' + label + '\n' + format_youtube_description(chapters);
       если главы пусты/невалидны — вернуть просто description."""
   ```
   `_maybe_compose_description` в панели становится тонкой обёрткой
   (берёт `tr("youtube_timecodes_label")` и пишет в `_desc_edit`).
3. **B11б:** консолидировать `_TAB_KEYS` и `_edit_map` — единый кортеж
   `(key, edit)` в порядке вкладок, из которого выводятся и карта индексов,
   и суффикс имени файла.
4. **B11в:** в тосте `youtube_saved` показывать не абсолютный путь, а
   `output/<имя_файла>` (локали менять не нужно — подставляется в `{path}`).
5. **B11а:** заменить `tests/test_lm_client.py::test_api_key_not_logged` на
   осмысленный тест (например: `repr(client)` и `str(client.__dict__)`... —
   если осмысленной проверки не получается, просто удалить тест).

**Приёмка:** юнит-тесты на `compose_full_description` (обычный случай, пустые
главы, пустое описание); все существующие тесты зелёные.

Коммит: `refactor: consolidate youtube panel tab map, compose helper, worker cleanup`.

---

## 3. Порядок коммитов

1. `fix: sample transcript evenly instead of truncating tail for insights` (2.1)
2. `fix: default generation language to detected transcript language` (2.2)
3. `fix: disconnect stale worker signals in youtube panel` (2.3)
4. `fix: stop insight and youtube workers on window close` (2.4)
5. `fix: surface generation errors in youtube tabs and finalize buttons` (2.5)
6. `fix: provider dialog no longer overrides panel provider selection` (2.6)
7. `fix: mask all secret fields in config CLI output` (2.7)
8. `fix: resolve output dir via core.paths for frozen builds` (2.8)
9. `fix: enforce YouTube 10-second minimum chapter gap in formatter` (2.9)
10. `refactor: consolidate youtube panel tab map, compose helper, worker cleanup` (2.10)

Каждый коммит: `ruff` чистый, `pytest` зелёный, изменённые файлы компилируются.

---

## 4. Критерии приёмки (итоговые)

- [ ] Для длинного видео (>45 мин) тайм-коды покрывают весь хронометраж, а не
      только начало (проверка: собрать промпт для транскрипта >48k символов и
      убедиться, что в него попали строки из последней трети записи).
- [ ] Русский транскрипт + язык «Авто» → главы/вопросы/описание на русском.
- [ ] Закрытие окна во время генерации — без крэша и предупреждений QThread.
- [ ] Ошибка провайдера видна в соответствующей вкладке; текст не упоминает
      LM Studio, если провайдер облачный; Copy/Save доступны, если есть контент.
- [ ] Смена провайдера возможна только из комбобокса панели; диалог настройки
      не меняет активного провайдера.
- [ ] `python config.py` не печатает ни одного секрета открытым текстом.
- [ ] Сохранение файлов работает и из исходников (`<project>/output/`), и
      логически корректно для frozen-сборки (юнит-тест).
- [ ] Главы с интервалом <10 с не попадают в вывод форматтера.
- [ ] `ruff check .` — 0 ошибок; `python -m pytest tests/ -q` — зелёный;
      headless-смоук MainWindow проходит.

---

## 5. Ручная проверка (после всех коммитов)

1. `lms server start` (модель `google/gemma-4-12b` уже загружена; CLI:
   `/Users/den/.lmstudio/bin/lms`).
2. Открыть запись `input/audio1170954204.m4a` из истории (History → открыть) —
   YouTube-вкладка активна, язык «Авто».
3. «Сгенерировать» → все 5 вкладок заполняются **на русском**; в Description —
   хук + абзац + «Тайм-коды:» + главы, покрывающие видео до ~48-й минуты.
4. «Сохранить в файл» → файл появляется в `output/`, тост показывает короткий путь.
5. Остановить LM Studio (`lms server stop`), нажать «Сгенерировать» → во
   вкладках видны понятные сообщения об ошибке, приложение живо.
6. Во время генерации закрыть окно → чистый выход без предупреждений в терминале.

---

## 6. Чего НЕ делать

- Не менять тексты промптов в `prompts/*.md` (включая `yt_description.md`).
- Не менять поведение по умолчанию (`lmstudio`, офлайн).
- Не рефакторить `ui/insights_panel.py`, `transcriber.py`, экспортёры.
- Не добавлять новые зависимости.
- Не коммитить содержимое `input/` и `output/`.
- Не логировать API-ключи ни в каком виде.
