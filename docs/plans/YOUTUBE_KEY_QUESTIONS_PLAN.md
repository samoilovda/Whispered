# План для Sonnet: тайм-коды «ключевых вопросов» для YouTube + выбор локального/облачного ИИ

> **Контекст.** Whispered — офлайн-приложение транскрипции на PyQt6 (whisper.cpp +
> локальный LLM через LM Studio). Задача: добавить генерацию **тайм-кодов по
> ключевым вопросам** беседы для описания YouTube, с возможностью выполнять
> генерацию как через **локальный LM Studio**, так и через **облачный API**
> (OpenAI-совместимый **или** Anthropic — на выбор пользователя).
> Документ самодостаточен. Читай его полностью до начала работы.
> Стиль кода, правила и способ проверки — как в `ROADMAP.md` §1.

---

## 0. Что УЖЕ есть (не переделывай это)

Значительная часть YouTube-функционала уже реализована и подключена — используй
её как фундамент, **не дублируй**:

| Компонент | Файл | Что делает |
|---|---|---|
| Форматтер тайм-кодов | `core/youtube_description.py` | `format_youtube_timestamp(sec)` и `format_youtube_description(chapters)` — превращают список `[{"start": int, "title": str}]` в YouTube-блок (первый пункт `0:00`, строго возрастающие). **Контент-агностичен** — «вопросы» лягут в него так же, как «главы». |
| Воркер генерации | `core/insights_worker.py` | `InsightsWorker(insight_type, segments, lm_url, language=None, parent=None)`. Типы в `_INSIGHT_TYPES`. Строит промпт из таймстампованных сегментов, зовёт `LMStudioClient.chat_completion_stream`, парсит JSON-массив (`_parse_json_response`) с одним ретраем. |
| UI-вкладка | `ui/youtube_panel.py` | Вкладка «YouTube» с кнопкой генерации, комбобоксом языка, внутренними под-вкладками (Chapters/Titles/Description/Tags), кнопкой «Копировать». Кнопка **включается только когда есть сегменты** (`set_segments`). |
| Клиент LLM | `core/lm_client.py` | `LMStudioClient(base_url)`. OpenAI-совместимый: POST `{base_url}/chat/completions`, SSE `data:`-парсинг, `choices[0].message.content`. **Без** авторизации и без поля `model`. |
| Интеграция | `ui/main_window.py` | `self.youtube_panel.set_segments(result.segments)` вызывается **только после готовой транскрипции** (`_on_finished` — строка ~843, и при загрузке из истории — ~917). Требование «только после готовой транскрипции с таймкодами» **уже выполнено**. |

**Вывод:** «кнопка, активная только после транскрипции» и «тайм-коды глав» — уже
готовы. Нетривиальная новая работа — это (1) **новый тип «ключевые вопросы»**,
(2) **облачные провайдеры** (OpenAI-совместимый + Anthropic), (3) **выбор
провайдера в UI YouTube-вкладки**.

---

## 1. Обязательные правила

1. **Офлайн — по-прежнему дефолт.** Провайдер по умолчанию — `lmstudio`.
   Из коробки поведение не меняется: никаких сетевых вызовов наружу, пока
   пользователь **явно** не выберет облачный провайдер и не введёт ключ.
   Правило `ROADMAP.md` §1.2.1 нужно скорректировать (см. задачу 4.9): облачный
   API допускается **только** как явно выбранная пользователем опция этой
   функции; телеметрии нет; по умолчанию всё локально.
2. **Приватность видима.** При выборе облачного провайдера в UI показать заметный
   нотис: «Транскрипт будет отправлен стороннему сервису (OpenAI/Anthropic)».
   Это privacy-first приложение — пользователь должен осознанно согласиться.
3. **Секреты защищены.** API-ключи хранятся в `config.json`
   (он уже пишется с правами `0600` — см. `config.py:save`). Дополнительно:
   - в UI поле ключа — `QLineEdit` с `EchoMode.Password` (образец — `hf_token`
     в `ui/settings_dialog.py`);
   - **никогда** не логировать ключи (ни целиком, ни в составе конфига);
   - не печатать ключ в сообщениях об ошибках.
4. **Не блокировать UI.** Вся генерация — в `QThread` (`InsightsWorker` уже
   такой). Сеть только внутри воркера.
5. **Отмена.** Существующий паттерн отмены в `YouTubePanel.clear()` сохранить.
   Для облака: между SSE-чанками (стриминг) — проверять `is_cancelled`; для
   не-стримингового пути (Anthropic) — проверять `is_cancelled` перед отправкой
   (urllib не умеет прерывать запрос в процессе — это норма, как в текущем
   `LMStudioClient.complete` non-stream).
6. **Тесты без сети.** Юнит-тесты не должны ходить в интернет: мокать клиента/
   `urllib`. Ставить моки как в `tests/test_text_processor.py` /
   `tests/test_youtube_description.py`.
7. **Один шаг — один коммит.** Формат: `feat: …`, `fix: …`, `refactor: …`.
8. **Не трогать.** `ui/insights_panel.py`, `prompts/chapters.md`, существующие
   типы инсайтов — оставить как есть (обратная совместимость).
9. **Проверка перед коммитом:** `ruff check .` (0 ошибок), `python -m pytest
   tests/ -q` (зелёный), `python -m compileall` изменённых файлов, и headless-
   смоук главного окна (см. §7).

---

## 2. Итоговая архитектура (что появится)

```
core/lm_client.py        — расширить: optional api_key (заголовок Authorization)
                           и optional model (поле payload). Один клиент теперь
                           обслуживает и LM Studio, и любой OpenAI-совместимый
                           облачный эндпоинт.
core/anthropic_client.py — НОВЫЙ: клиент Anthropic Messages API с тем же
                           интерфейсом chat_completion_stream(...).
core/ai_provider.py      — НОВЫЙ: ProviderSettings + create_client() (фабрика)
                           + provider_from_config(cfg). Единая точка выбора.
core/insights_worker.py  — новый тип "yt_questions"; optional provider-параметр.
prompts/yt_questions.md  — НОВЫЙ промпт «ключевые вопросы → JSON [{start,title}]».
config.py                — новые поля yt_* (провайдер, base_url, ключи, модели).
ui/provider_dialog.py    — НОВЫЙ маленький QDialog для настройки облачного
                           провайдера (base URL / API-ключ / модель).
ui/youtube_panel.py      — комбобокс провайдера + кнопка «Настроить…» + новая
                           под-вкладка «Ключевые вопросы» + ветвление в _generate.
locales/en.json, ru.json — новые ключи UI.
ROADMAP.md               — правка §1.2.1 (карман-исключение для облака).
tests/                   — новые тесты (см. §4.10).
```

Ключевая идея: **LM Studio и облачный OpenAI-совместимый — это один код-путь**
(оба OpenAI-shaped, отличаются base_url, наличием `Authorization` и поля
`model`). Anthropic — отдельный адаптер с тем же интерфейсом. `InsightsWorker`
остаётся провайдер-агностичным: он зовёт `client.chat_completion_stream(...)`.

---

## 3. Формат данных «ключевых вопросов»

Тот же shape, что у глав: JSON-массив `[{"start": int_seconds, "title": str}]`,
где `title` — **переформулированный ключевой вопрос** беседы. Благодаря этому
переиспользуется `format_youtube_description()` без изменений (0:00 в начале,
возрастание, YouTube-совместимость). Пример желаемого вывода в UI:

```
0:00 О чём вообще этот разговор
12:05 Как систематизировать жизнь и не сделать её мёртвой
24:40 Где граница между структурой и хаосом
...
```

---

## 4. Задачи (в порядке реализации)

### 4.1. `core/lm_client.py` — поддержать ключ и модель

- В `LMStudioClient.__init__(self, base_url=DEFAULT_LM_STUDIO_URL, api_key: str = "", model: str = "")`
  сохранить `self._api_key`, `self._model`.
- В `chat_completion_stream` и в non-stream ветке `complete`:
  - если `self._api_key` — добавить заголовок `"Authorization": f"Bearer {self._api_key}"`;
  - если `self._model` — добавить в payload ключ `"model": self._model`.
    (LM Studio лишнее поле `model` игнорирует/принимает; облаку оно обязательно.)
- Не менять сигнатуры публичных методов, не ломать существующих вызывающих
  (`text_processor`, `article_generator`, `chat_worker`, `insights_worker`,
  `book_pipeline` создают `LMStudioClient(url)` — новые параметры опциональны).
- **Не логировать** `api_key`.

**Приёмка:** существующие тесты зелёные; при переданном `api_key` в запросе
появляется заголовок Authorization (проверяемо юнит-тестом с моком `urllib`).

### 4.2. `core/anthropic_client.py` — новый адаптер (тот же интерфейс)

Класс `AnthropicClient` с методом-двойником:
```python
def chat_completion_stream(self, messages, max_tokens=1024, temperature=0.2,
                           timeout=300, on_token=None, is_cancelled=None) -> Optional[str]
```
Реализация — **не-стриминговый** POST (проще и надёжнее; YouTube-панель не
показывает токены по одному, воркер ждёт полный JSON):
- Эндпоинт: `POST https://api.anthropic.com/v1/messages`.
- Заголовки: `x-api-key: <key>`, `anthropic-version: 2023-06-01`,
  `content-type: application/json`.
- Тело: `{"model": <model>, "max_tokens": <max_tokens>, "system": <system_text>,
  "messages": <user_messages>, "temperature": <t>}`. **Важно:** Anthropic держит
  системный промпт в отдельном верхнеуровневом поле `system`, а не в `messages`.
  Поэтому: извлечь из входного `messages` все элементы с `role == "system"`,
  склеить их в строку `system`, остальное (`user`/`assistant`) отдать как
  `messages`. Если системных нет — `system` не добавлять.
- Ответ: `{"content": [{"type":"text","text": "..."}], ...}` → склеить `text`
  всех блоков с `type == "text"`.
- Отмена: `if is_cancelled and is_cancelled(): return None` **до** отправки.
- Ошибки/таймаут: как в `LMStudioClient` — логировать (без ключа!) и вернуть `None`.
- Актуальные ID моделей и заголовки Anthropic **сверить через скилл
  `claude-api`** перед реализацией (не полагаться на память).

**Приёмка:** юнит-тест с моком `urllib` проверяет, что (а) системное сообщение
уходит в поле `system`, а не в `messages`; (б) склейка текстовых блоков верна.

### 4.3. `core/ai_provider.py` — фабрика провайдеров

```python
@dataclass
class ProviderSettings:
    kind: str            # "lmstudio" | "openai" | "anthropic"
    base_url: str = ""
    api_key: str = ""
    model: str = ""

def create_client(ps: ProviderSettings):
    if ps.kind == "anthropic":
        from core.anthropic_client import AnthropicClient
        return AnthropicClient(api_key=ps.api_key, model=ps.model)
    # lmstudio и openai — один OpenAI-совместимый клиент
    from core.lm_client import LMStudioClient
    return LMStudioClient(base_url=ps.base_url, api_key=ps.api_key, model=ps.model)

def provider_from_config(cfg) -> ProviderSettings:
    # Собирает ProviderSettings ДЛЯ YOUTUBE-функции из полей Config (см. 4.5).
    # kind == "lmstudio" → base_url = cfg.lm_studio_url, без ключа/модели.
    # kind == "openai"   → base_url/key/model из yt_openai_*.
    # kind == "anthropic"→ key/model из yt_anthropic_*.
```
Оба клиента обязаны предоставлять один и тот же метод
`chat_completion_stream(messages, *, is_cancelled=..., temperature=...,
max_tokens=..., timeout=..., on_token=None) -> Optional[str]`.

**Приёмка:** `create_client` возвращает корректный класс по `kind`;
`provider_from_config` маппит поля без потерь (юнит-тест без сети).

### 4.4. `core/insights_worker.py` — новый тип + провайдер

- В `_INSIGHT_TYPES` добавить `"yt_questions"`.
- В `InsightsWorker.__init__` добавить параметр
  `provider: Optional["ProviderSettings"] = None` (после `language`), сохранить в
  `self._provider`. **Не менять** порядок существующих позиционных аргументов —
  `ui/insights_panel.py` и прочие вызовы (`lm_url` без провайдера) должны
  работать по-старому.
- В `_execute()`: `client = create_client(self._provider) if self._provider
  else LMStudioClient(self._lm_url)`. Остальная логика (промпт, парсинг JSON,
  ретрай, fallback на сырой текст) — без изменений; она уже провайдер-агностична,
  т.к. работает через `chat_completion_stream`.
- `_build_prompt_text` уже грузит промпт через `load_prompt(insight_type)` —
  новый тип подхватит `prompts/yt_questions.md` автоматически.

**Приёмка:** вкладка Insights (передаёт только `lm_url`) не изменила поведение;
новый тип `yt_questions` возвращает список `[{start,title}]`.

### 4.5. `prompts/yt_questions.md` — новый промпт

По образцу `prompts/chapters.md`, но вытягивать **ключевые вопросы беседы**, а не
темы. Требования к тексту промпта:
- Проанализировать таймстампованный транскрипт разговора и выделить ключевые
  **вопросы/проблемы**, которые обсуждались (то, вокруг чего строится беседа).
- Вернуть **только** валидный JSON-массив, без markdown-заборов и пояснений.
  Каждый элемент: `"start"` (int, секунды из ближайшего сегмента) и `"title"`
  (краткая формулировка вопроса, 4–10 слов, с «?» где уместно).
- Требования YouTube к главам: **≥3** пунктов, шаг между тайм-кодами **≥10 с**,
  первый — около начала (форматтер всё равно принудит `0:00`).
- Формулировать на языке беседы, если директива языка не задана.
- Пример: `[{"start": 0, "title": "О чём этот разговор"}, {"start": 725,
  "title": "Как не сделать систему мёртвой?"}]`.

### 4.6. `config.py` — новые поля (только для YouTube-функции)

Добавить поля в dataclass `Config` (значения по умолчанию — чтобы старые конфиги
грузились без потерь; фильтрация неизвестных полей в `load()` уже есть):
```python
# YouTube AI provider (feature-scoped; local LM Studio stays the default)
yt_provider: str = "lmstudio"                       # "lmstudio" | "openai" | "anthropic"
yt_openai_base_url: str = "https://api.openai.com/v1"
yt_openai_api_key: str = ""
yt_openai_model: str = "gpt-4o-mini"                # editable default
yt_anthropic_api_key: str = ""
yt_anthropic_model: str = "claude-sonnet-5"         # editable default; сверить через claude-api
```
Модели-дефолты — **редактируемые**; актуальные ID сверить через скилл `claude-api`.
Ключи наследуют защиту `0600` (файл уже chmod-ится в `save()`).

**Приёмка:** round-trip save/load сохраняет новые поля; старый config.json без
этих полей грузится на дефолтах (юнит-тест в `tests/test_config.py`).

### 4.7. `ui/provider_dialog.py` — маленький диалог настройки провайдера

Отдельный `QDialog` (низкий риск, не трогаем большой `settings_dialog.py`):
- Заголовок «Настройка облачного провайдера».
- Радио/комбо выбора: OpenAI-совместимый / Anthropic (синхронизирован с тем, что
  выбрано во вкладке — можно передавать текущий `kind`).
- Поля для **OpenAI-совместимого**: Base URL, API Key (**маскированный**,
  кнопка «показать»), Model.
- Поля для **Anthropic**: API Key (маскированный), Model.
- Кнопки OK/Cancel. Запись в `Config` только по OK (`save_config()`).
- (Опционально) кнопка «Проверить соединение» — асинхронно, по образцу проверки
  в `ui/book_panel.py`; при успехе — имя модели/200, при ошибке — текст (без ключа).

Альтернатива (если решишь так): секция «YouTube AI» в существующем
`ui/settings_dialog.py`. Но отдельный диалог предпочтительнее — меньше риска для
уже работающего окна настроек.

### 4.8. `ui/youtube_panel.py` — провайдер в UI + новая под-вкладка

- В `_YT_TYPES` добавить `"yt_questions"` (или сгенерировать его отдельно —
  на твоё усмотрение, но он должен идти тем же путём, что `chapters`).
- В ряд контролов добавить:
  - `QComboBox` «Провайдер»: «LM Studio (локально)» (data=`"lmstudio"`),
    «OpenAI-совместимый» (data=`"openai"`), «Anthropic» (data=`"anthropic"`).
    Начальное значение — из `get_config().yt_provider`; при смене — писать в
    конфиг.
  - Кнопку «Настроить…» — открывает `ProviderDialog`; активна только когда
    выбран облачный провайдер.
  - Нотис приватности (лейбл), видимый только при облачном провайдере.
- Новая под-вкладка `QPlainTextEdit` «Ключевые вопросы»
  (`tr("yt_tab_questions")`), read-only, моноширинный — как соседние.
- В `_generate()`:
  1. `provider = provider_from_config(get_config())`.
  2. Если `provider.kind != "lmstudio"` и ключ пуст → показать локализованное
     сообщение (`youtube_no_api_key`) и **не** запускать воркеры.
  3. Если `provider.kind == "lmstudio"` и `lm_studio_url` пуст → как сейчас
     (`youtube_no_lm`).
  4. Создавать воркеры: `InsightsWorker(yt_type, self._segments, lm_url,
     language=lang, provider=(None if kind=="lmstudio" else provider),
     parent=self)`.
- В `_on_finished`: для `"yt_questions"` — если `isinstance(data, list)` →
  `format_youtube_description(data)` в поле «Ключевые вопросы» (пусто →
  `youtube_empty`); иначе сырой текст/`youtube_error`. (Точно тот же паттерн, что
  для `"chapters"`.)
- «Копировать» и жизненный цикл `clear()` (отмена воркеров, `wait`) — сохранить.

### 4.9. `ROADMAP.md` — снять противоречие с офлайн-правилом

Отредактировать §1.2 правило 1: добавить явное исключение — облачный API
допускается **только** как опционально выбранная пользователем возможность
конкретной функции (YouTube), при явном вводе ключа; по умолчанию всё локально,
телеметрии нет. Иначе код и правила будут противоречить друг другу.

### 4.10. Тесты (без сети)

- `tests/test_ai_provider.py`: `create_client` возвращает нужный класс по `kind`;
  `provider_from_config` корректно маппит поля.
- `tests/test_anthropic_client.py`: с моком `urllib.request.urlopen` —
  системное сообщение уходит в `system` (не в `messages`); склейка текстовых
  блоков ответа; отмена до отправки возвращает `None`; ключ не попадает в логи.
- `tests/test_lm_client.py` (новый или дополнить): при `api_key` в запросе есть
  `Authorization`; при `model` — поле `model` в payload; без них — как раньше.
- `tests/test_config.py`: round-trip новых `yt_*` полей; старый конфиг без них.
- `tests/test_youtube_description.py`: уже покрывает форматтер на shape
  `[{start,title}]` — при желании добавить кейс «вопросы».
- Мок-паттерн Qt/`core` — как в `tests/test_text_processor.py`.

---

## 5. Порядок коммитов

1. `feat: extend LMStudioClient with optional api_key and model` (4.1 + тест).
2. `feat: add Anthropic Messages API client` (4.2 + тест).
3. `feat: add AI provider factory for youtube feature` (4.3 + тест).
4. `feat: add yt_questions insight type and provider support to InsightsWorker` (4.4 + промпт 4.3→4.5).
5. `feat: add youtube AI provider config fields` (4.6 + тест).
6. `feat: add cloud provider dialog` (4.7).
7. `feat: wire provider selection and key-questions tab into youtube panel` (4.8 + локали 4.8→4.9→locales).
8. `docs: allow opt-in cloud API for youtube feature in ROADMAP rules` (4.9).

Каждый коммит: `ruff` чистый, `pytest` зелёный, изменённые файлы компилируются.

---

## 6. Критерии приёмки

- [ ] По умолчанию (`yt_provider="lmstudio"`) поведение не изменилось; сеть наружу
      не идёт; вкладка Insights работает как раньше.
- [ ] Кнопка генерации активна **только** после готовой транскрипции с
      таймстампами (существующее поведение сохранено).
- [ ] Новая под-вкладка «Ключевые вопросы» выдаёт валидный YouTube-блок:
      первый пункт `0:00`, тайм-коды строго возрастают, ≥3 пункта, заголовки —
      осмысленные вопросы беседы.
- [ ] Провайдер переключается в UI YouTube-вкладки; при облачном провайдере без
      ключа — понятное сообщение, воркеры не стартуют.
- [ ] OpenAI-совместимый провайдер: запрос уходит с `Authorization: Bearer` и
      полем `model`.
- [ ] Anthropic: системный промпт в поле `system`, ответ собирается из текстовых
      блоков; генерация вопросов работает.
- [ ] При выборе облачного провайдера виден нотис о передаче транскрипта третьей
      стороне.
- [ ] API-ключи: поле маскировано, файл конфига `0600`, ключей нет в логах.
- [ ] `ruff check .` — 0 ошибок; `pytest tests/` — зелёный; новые модули покрыты
      юнит-тестами без сети.

---

## 7. Ручная проверка (на примере из `input/`)

1. Запустить приложение (`./run-mac.sh`) или headless-смоук:
   `QT_QPA_PLATFORM=offscreen python -c "..."` — окно создаётся без ошибок.
2. Транскрибировать `input/audio1170954204.m4a` (язык «Авто» — уже чинён; выйдет
   полный русский транскрипт ~2258 сегментов) **или** открыть эту запись из
   истории. Убедиться, что YouTube-вкладка разблокировалась.
3. Провайдер = **LM Studio**: сгенерировать «Ключевые вопросы» → валидный блок с
   тайм-кодами (0:00 первым, возрастание), «Копировать» кладёт текст в буфер.
4. Провайдер = **OpenAI-совместимый**: ввести base URL/ключ/модель в «Настроить…»,
   сгенерировать — результат приходит; при пустом ключе — понятное сообщение.
5. Провайдер = **Anthropic**: ввести ключ/модель, сгенерировать — результат
   приходит; проверить, что вопросы осмысленные и тайм-коды валидны.
6. Проверить нотис приватности при облачных провайдерах и маскировку ключа.

> Тестовые API-ключи в репозиторий/логи не попадают. Реальные ключи вводит
> пользователь в диалоге; они хранятся только в `~/.local/share/Whispered/config.json`
> (или платформенный аналог) с правами `0600`.

---

## 8. Чего НЕ делать

- Не делать облако дефолтом; не отправлять ничего наружу без явного выбора
  провайдера и ключа.
- Не распространять выбор провайдера на другие AI-функции (чистка текста,
  статьи, чат, инсайты, книжный пайплайн) — они остаются на LM Studio. Поля и
  логика — с префиксом `yt_`, только для YouTube.
- Не переименовывать `LMStudioClient` (потянет за собой десяток импортов) — только
  расширить.
- Не переписывать `ui/insights_panel.py`, `prompts/chapters.md`, существующие типы
  инсайтов.
- Не логировать API-ключи ни в каком виде.
- Не создавать PR и не пушить в удалённые ветки без отдельной просьбы.
