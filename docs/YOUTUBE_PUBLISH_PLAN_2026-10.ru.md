# Публикация на YouTube после рецепта «Video for YouTube» — план разработки

Статус: к исполнению. Дата: 2026-10-07.
Исполнитель: ИИ-агент (Claude Code или аналог). Читать целиком до начала работы.

## 0. Цель

После прогона рецепта `youtube_video` (`transcribe → clean → youtube_package → cover`,
см. `domain/recipe.py`) пользователь одним действием отправляет видео в свой канал:

- **Фаза A — «Передача» (handoff).** Диалог «Опубликовать на YouTube»: выбранное
  название, итоговое описание, теги, обложка; кнопки «Скопировать…», «Показать видео
  в Finder», «Открыть YouTube Studio». Без сети со стороны приложения, без OAuth.
- **Фаза B — «Загрузка через API».** Тот же диалог получает кнопку «Загрузить»:
  OAuth 2.0 пользователя → resumable upload через YouTube Data API v3 → обложка через
  `thumbnails.set` → открывается `https://studio.youtube.com/video/<id>/edit`, где
  пользователь проверяет и публикует вручную.

Фаза A — самостоятельная ценность и фундамент для B (общий диалог, общая модель
«пакета публикации»). Делать строго по порядку: A полностью, затем B.

## 1. Перед началом

1. `git status`. В рабочем дереве могут быть чужие незакоммиченные изменения
   (например, в `ui/youtube_panel.py`, `core/youtube_description.py`). **Не коммить
   и не откатывай их.** Если они пересекаются с файлами этого плана — остановись и
   спроси пользователя.
2. Прочитай `CLAUDE.md` (правила 1–8, pre-commit gate, mypy-блокирующие модули).
3. Прочитай, чтобы понять существующие паттерны:
   - `application/steps.py` — `StepContext`, `_youtube_package_runner`
     (пишет `youtube_package.json` в `artifact_dir`), `_cover_runner` (`cover.png`).
   - `ui/main_window.py` — `_on_recipe_step_finished` / `_on_recipe_job_finished`
     (около строк 2100–2200), `_recipe_context`, `_STEP_TO_ARTIFACT_TYPE`.
   - `ui/youtube_panel.py` — `_TAB_SPECS`, `set_result()`, `_maybe_compose_description()`,
     `_copy_to_clipboard()`; панель — источник **отредактированных** пользователем текстов.
   - `core/youtube_description.py` — `compose_full_description()`, `check_chapters()`.
   - `ui/run_view.py` — `RunView.set_finished()`, кнопка `run_open_record`.
   - `core/base_worker.py`, `core/worker_registry.py`, `core/insights_worker.py` (паттерн
     `_disconnect_business_signals`), `core/secrets_store.py`, `config.py` (`_SECRET_FIELDS`).
   - `ui/i18n_helpers.py` (`Retranslator`), `locales/en.json`, `locales/ru.json`.

## 2. Ключевые архитектурные решения (не пересматривать без пользователя)

1. **Публикация — НЕ шаг `JobEngine`.** Не добавлять `youtube_upload` в
   `STEP_REGISTRY`/`KNOWN_STEP_NAMES`. Причины: шаги кэшируются по `Artifact` и
   перезапускаются через Retry — для внешнего необратимого действия это означает
   дубли видео на канале; кроме того, публикации нужно подтверждение пользователя
   (выбор одного из нескольких сгенерированных названий). Публикация — пост-этап
   прогона в UI-слое.
2. **Запуск после прогона.** Новое поле `Config.yt_publish_mode: str = "off"`
   (`"off" | "handoff" | "api"`). Если режим не `off`, рецепт содержит шаг
   `youtube_package` и он завершился SUCCEEDED/SKIPPED — по `job_finished`
   автоматически открывается диалог публикации. Независимо от режима в `RunView`
   после завершения появляется кнопка «Опубликовать на YouTube» (если есть пакет).
   В Library/YouTube-панели — то же действие для ранее обработанной записи.
3. **Источник текстов — `YouTubePanel`, а не JSON на диске.** Пользователь мог
   отредактировать описание/теги во вкладках. Добавь в панель публичный метод
   `publish_texts() -> dict` (titles: list[str], description: str — уже с
   таймкодами, tags: list[str]). Если панель пуста — fallback на
   `youtube_package.json` через `load_step_result`.
4. **Без новых тяжёлых зависимостей.** OAuth (installed-app, loopback + PKCE) и
   resumable upload пишутся поверх уже имеющегося `requests` (~300 строк). Не
   тянуть `google-api-python-client` / `google-auth-oauthlib`: это discovery-документы,
   PyInstaller-hiddenimports и лишний вес в frozen-сборке.
5. **Безопасные значения по умолчанию.** `privacyStatus="private"`, режим `off`,
   API-режим требует явного подключения аккаунта в Settings. Пункт «public» в
   диалоге не предлагать (только private / unlisted) — публикация делается в Studio.
6. **Секреты.** Refresh-токен — только в OS keyring через `core/secrets_store.py`
   (`set_secret("youtube_refresh_token", …)`), никогда не в `config.json`. Если
   keyring недоступен — токен живёт только в памяти до выхода, в Settings показать
   предупреждение. `client_secret` desktop-клиента — в `_SECRET_FIELDS`
   (`yt_oauth_client_secret`), `client_id` — обычное поле Config.
7. **Слои.**
   - `domain/youtube_publish.py` — Qt-free DTO `PublishPackage`, `UploadRecord`.
   - `application/youtube_publish.py` — сборка/валидация пакета (может импортировать
     `core.youtube_description`).
   - `core/youtube_oauth.py`, `core/youtube_upload.py` — сетевой код, Qt-free, тестируемый
     с фейковым HTTP-сессионным объектом.
   - `core/youtube_upload_worker.py` — `BaseWorker`-обёртка.
   - `ui/youtube_publish_dialog.py` — диалог.
   Все новые модули в `core/`, `domain/`, `application/` автоматически попадают в
   mypy-блокирующий набор — пиши их типизированными с первого коммита.

## 3. Фаза A — «Передача» (handoff)

### A1. `docs:` обновить правило сети (одна строка) — не нужно для фазы A

Фаза A не делает сетевых запросов из приложения (открытие URL в системном браузере —
не сеть приложения). CLAUDE.md не трогать до B1.

### A2. `feat:` модель пакета публикации

`domain/youtube_publish.py`:

```python
@dataclass(frozen=True)
class PublishPackage:
    video_path: Path
    title: str
    description: str
    tags: tuple[str, ...]
    thumbnail_path: Path | None
    privacy: str = "private"          # "private" | "unlisted"
    language: str | None = None       # snippet.defaultLanguage / defaultAudioLanguage
    category_id: str = "22"           # People & Blogs; редактируемо позже
    made_for_kids: bool = False

@dataclass(frozen=True)
class UploadRecord:                   # для фазы B, но объявить сразу
    video_id: str
    uploaded_at: str                  # ISO-8601 UTC
    title: str
    privacy: str
```

`application/youtube_publish.py`:

- `normalize_titles(raw: list[str] | str) -> list[str]` — убрать нумерацию `"1. "`,
  кавычки-ёлочки/прямые по краям, пустые строки, дубли.
- `parse_tags(raw: list[str] | str) -> list[str]` — разделители `,` и перевод строки,
  убрать `#`, trim, дедуп без учёта регистра.
- `build_package(*, video_path, titles, title_index, description, tags, cover_path,
  language) -> PublishPackage`.
- `validate_package(pkg) -> list[PublishIssue]` (issue = `kind` + i18n-ключ + параметры),
  правила YouTube:
  - файл существует и расширение видео (используй `utils.py`-набор видео-расширений,
    не дублируй список); аудио-источник → issue `source_not_video`;
  - title: непусто, ≤ 100 символов, без `<` и `>`;
  - description: ≤ 5000 **байт** UTF-8, без `<` и `>`;
  - tags: сумма длин ≤ 500 символов (тег с пробелом считается +2 за кавычки);
    лишние теги отбрасываются с конца с issue-предупреждением, а не ошибкой;
  - thumbnail: существует; > 2 МБ → issue `thumbnail_too_large` (конвертация в
    JPEG делается в UI-слое через `QImage`, см. B5).
- `find_video_source(source_path) -> Path | None` — если запись транскрибировалась
  из видео, это `StepContext.source_path`. Если из извлечённого аудио — проверить,
  где проект хранит исходный путь (history record / Library); если нигде — диалог
  предлагает выбрать файл вручную (`QFileDialog`).

Тесты: `tests/test_youtube_publish.py` — нумерация названий, теги с `#` и дублями,
лимит 500 символов тегов, байтовый лимит описания на кириллице, угловые скобки,
аудио-источник.

### A3. `feat:` `YouTubePanel.publish_texts()`

Возвращает текущие (отредактированные) тексты вкладок; описание — после
`_maybe_compose_description()` (то есть с таймкодами). Тест в `tests_qt/`.

### A4. `feat:` диалог публикации (режим handoff)

`ui/youtube_publish_dialog.py`, `YouTubePublishDialog(QDialog)`, открывается `.exec()`
(по соглашению CLAUDE.md модальные диалоги не ретранслируются).

Содержимое:
- редактируемый `QComboBox` с названиями + счётчик `n/100`;
- `QPlainTextEdit` описания + счётчик байт `n/5000`;
- поле тегов + счётчик `n/500`;
- превью обложки (если есть), путь к видео (+ кнопка «Выбрать…», если не найдено);
- блок предупреждений из `validate_package` и из `check_chapters` (например «главы не
  покажутся на YouTube: первая не с 0:00»);
- кнопки handoff:
  - «Скопировать название», «Скопировать описание», «Скопировать теги» →
    `QApplication.clipboard()`; toast/статус «Скопировано»;
  - «Показать видео в Finder» → macOS `open -R <path>`, Windows
    `explorer /select,<path>`, Linux — открыть папку через `QDesktopServices`.
    Вынести в маленький хелпер `core/platform_support.py::reveal_in_file_manager`
    (Qt-free, через `subprocess`, без `shell=True`);
  - «Сохранить пакет в папку» → `<output>/<имя>_youtube/` с `title.txt`,
    `description.txt`, `tags.txt`, `cover.png` (удобно перетаскивать обложку в Studio);
  - «Открыть YouTube Studio» → `QDesktopServices.openUrl("https://www.youtube.com/upload")`
    (редиректит в диалог загрузки Studio на нужный канал).
- Кнопка «Загрузить на YouTube» (фаза B) — скрыта, пока режим не `api` или аккаунт
  не подключён.

Все строки — через `tr()`, ключи в `locales/en.json` и `locales/ru.json` с префиксом
`yt_publish_*`.

Тест: `tests_qt/test_youtube_publish_dialog.py` — заполнение из `publish_texts()`,
кнопки копирования кладут правильный текст в буфер, `openUrl`/`reveal` вызываются
(monkeypatch), кнопка API скрыта без подключения, счётчики и предупреждения.

### A5. `feat:` интеграция с прогоном

- `config.py`: `yt_publish_mode: str = "off"` + валидация допустимых значений (как
  `yt_provider`). Тест в `tests/test_config.py`.
- `RunView`: сигнал `publish_requested`, кнопка «Опубликовать на YouTube» рядом с
  `run_open_record`, видима при `set_finished(True)` и наличии пакета
  (`set_publish_available(bool)`); ретрансляция в `_retranslate`.
- `MainWindow._on_recipe_job_finished`: если `youtube_package` в `succeeded` и
  `config.yt_publish_mode != "off"` — `QTimer.singleShot(0, self._open_publish_dialog)`
  (после того, как UI обновился). `_open_publish_dialog` собирает данные из
  `self.youtube_panel.publish_texts()`, `cover.png` из `artifact_dir`,
  `source_path` из `_recipe_context`.
- То же действие — в YouTube-панели (кнопка рядом с «Копировать») и в командной
  палитре (`ui/command_palette.py`), чтобы опубликовать уже готовую запись.
- Settings → раздел YouTube: выбор режима «Выкл / Передача / Загрузка через API».

Тест: `tests_qt/` — после завершения рецепта с `youtube_package` при режиме `handoff`
диалог открывается (monkeypatch `exec`), при `off` — нет; при упавшем
`youtube_package` — нет.

**Критерий готовности фазы A:** реальный прогон рецепта на видео → диалог
открывается сам → копирование трёх полей + Finder + Studio работают; pre-commit
gate зелёный.

## 4. Фаза B — загрузка через YouTube Data API v3

### B1. `docs:` исключение в правиле «Offline first»

В `CLAUDE.md`, правило 1, добавить к разрешённой сети: «и для явной загрузки видео
пользователем на его канал YouTube (`core/youtube_oauth.py`, `core/youtube_upload.py`;
по умолчанию выключено, только по нажатию кнопки)». Также строку в карту архитектуры
(`core/`). Отдельный коммит.

### B2. `feat:` OAuth 2.0 для desktop-приложения

`core/youtube_oauth.py`, только `requests` + stdlib:

- Константы: `AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"`,
  `TOKEN_URL = "https://oauth2.googleapis.com/token"`,
  `REVOKE_URL = "https://oauth2.googleapis.com/revoke"`,
  `SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
  "https://www.googleapis.com/auth/youtube.readonly")` — readonly нужен для показа
  имени канала (`channels.list?mine=true`). Перед реализацией сверь со свежей
  документацией, что `thumbnails.set` разрешён скоупом `youtube.upload`; если нет —
  используй `https://www.googleapis.com/auth/youtube`.
- Flow (RFC 8252, loopback + PKCE S256 + `state`):
  1. `http.server` на `127.0.0.1:0`, `redirect_uri=http://127.0.0.1:<port>`;
  2. открыть системный браузер (`webbrowser.open` или колбэк из UI);
  3. цикл `server.handle_request()` с `server.timeout = 0.5`, проверяя
     `is_cancelled()` и общий таймаут (5 мин) — **это и делает вход отменяемым**;
  4. проверить `state`, обменять `code` на токены (`grant_type=authorization_code`,
     `code_verifier`), ответить в браузер простой HTML-страницей «Можно закрыть вкладку»;
  5. сохранить `refresh_token` через `secrets_store.set_secret("youtube_refresh_token")`.
- `TokenProvider.access_token()` — кэш access-токена до `expires_in - 60 с`, refresh
  через `grant_type=refresh_token`; `invalid_grant` → исключение
  `YouTubeAuthExpired` (UI предложит переподключить).
- `disconnect()` — revoke + `delete_secret`.
- Таймауты `requests`: всегда явные (`timeout=(10, 60)`). См. память проекта про
  сокеты LM Studio — к YouTube не относится, но короткие read-таймауты на upload-чанках
  не ставить.
- Config: `yt_oauth_client_id: str = ""`, `yt_oauth_client_secret: str = ""`
  (добавить в `_SECRET_FIELDS`), `yt_channel_title: str = ""` (только для показа).
  Settings: кнопка «Импортировать client_secret.json» (парсит `installed.client_id`
  / `installed.client_secret`), «Подключить аккаунт», «Отключить», статус «Подключено:
  <канал>». Вход выполняется в `BaseWorker`, зарегистрированном в `WorkerRegistry`.

Тесты `tests/test_youtube_oauth.py` (без сети): построение auth-URL (PKCE, state,
scopes, `access_type=offline`, `prompt=consent`), обмен кода с фейковой сессией,
несовпадение `state`, refresh, `invalid_grant` → `YouTubeAuthExpired`, отмена во
время ожидания редиректа, хранение токена через monkeypatch `secrets_store`.

### B3. `feat:` resumable upload

`core/youtube_upload.py`, Qt-free, сессия инжектируется (протокол с методами
`post`/`put`), чтобы тестировать без сети.

1. Инициализация: `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`,
   заголовки `Authorization: Bearer …`, `X-Upload-Content-Type: video/*`,
   `X-Upload-Content-Length: <size>`, тело JSON:
   ```json
   {"snippet": {"title": "...", "description": "...", "tags": [...],
                "categoryId": "22", "defaultLanguage": "ru", "defaultAudioLanguage": "ru"},
    "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": false}}
   ```
   Ответ: `Location` = session URI.
2. Чанки по 8 МиБ (кратно 256 КиБ): `PUT <session>` с `Content-Range: bytes a-b/total`.
   `308` → прочитать `Range: bytes=0-N`, продолжить с `N+1` (НЕ с конца отправленного
   чанка). `200/201` → JSON ресурса видео, взять `id`.
3. Между чанками: `is_cancelled()` → прекратить; прогресс `(sent/total)` колбэком.
4. Ошибки:
   - `5xx`, `ConnectionError`, `Timeout` → экспоненциальный backoff (1, 2, 4… до 64 с,
     не больше 8 попыток), затем запрос статуса `PUT` с `Content-Range: bytes */total`
     и продолжение с подтверждённого байта;
   - `401` → один раз обновить токен и повторить;
   - `403` с `reason` `quotaExceeded` / `uploadLimitExceeded` / `forbidden` →
     типизированные исключения с понятным i18n-сообщением, без повторов;
   - `404/410` на session URI → сессия истекла, начать заново (с подтверждения в UI).
5. Сохранять session URI + путь + размер + mtime в `artifact_dir/youtube_upload.pending.json`,
   чтобы после краха/закрытия приложения предложить «Продолжить загрузку» (session URI
   живёт около недели). Удалять файл после успеха.
6. Обложка: `POST https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=<id>&uploadType=media`,
   тело — байты PNG/JPEG, `Content-Type` по типу. Ошибка обложки (например, `403` —
   канал не подтверждён по телефону) — **предупреждение**, не провал загрузки.
7. Результат: `UploadRecord`, записать в `artifact_dir/youtube_upload.json`
   (атомарно, как `infrastructure/persistence/artifact_store.py`).

Тесты `tests/test_youtube_upload.py` с фейковой сессией: полный успешный путь из
нескольких чанков, `308` с частичным `Range`, `503` → backoff (sleep инжектируется) →
запрос статуса → продолжение, `401` → refresh, квота → нужное исключение, отмена
между чанками, сбой обложки не роняет результат, pending-файл создаётся и удаляется.

### B4. `feat:` worker загрузки

`core/youtube_upload_worker.py`, `YouTubeUploadWorker(BaseWorker)`:
сигналы `progress(int, str)`, `uploaded(object)` (`UploadRecord`), `failed(str)`,
`thumbnail_warning(str)`. Не называй сигнал `finished` — иначе нужен
`_disconnect_business_signals()` (см. правило 3). Регистрировать в
`WorkerRegistry`; `MainWindow.closeEvent` уже вызывает `shutdown_all` — проверь, что
отмена прерывает загрузку между чанками за разумное время (чанк 8 МиБ на медленном
канале — это секунды; при необходимости уменьши чанк до 4 МиБ).

### B5. `feat:` загрузка из диалога

- Кнопка «Загрузить на YouTube» в `YouTubePublishDialog`, видима при
  `yt_publish_mode == "api"` и подключённом аккаунте; выбор private/unlisted
  (по умолчанию private).
- Обложка > 2 МБ → перекодировать через `QImage` в JPEG (качество 90) во временный
  файл в `artifact_dir`.
- **Защита от дублей:** если `youtube_upload.json` уже есть — подтверждение
  «Эта запись уже загружена (<id>, <дата>). Загрузить ещё раз?» с кнопкой
  «Открыть в Studio».
- Во время загрузки: прогресс в диалоге и в статус-баре; диалог можно закрыть —
  загрузка продолжается в фоне (worker живёт в `MainWindow`, а не в диалоге), есть
  кнопка отмены.
- Успех: `QDesktopServices.openUrl("https://studio.youtube.com/video/<id>/edit")`,
  toast, бейдж артефакта `youtube_upload` в истории (по аналогии с
  `_STEP_TO_ARTIFACT_TYPE`), Library показывает ссылку.
- Если pending-файл найден при открытии диалога — предложить продолжить.
- Неверифицированный проект: в диалоге постоянная подсказка «Видео из
  неверифицированных API-проектов Google блокирует в статусе private до аудита
  проекта» со ссылкой на справку в Settings.

Тесты `tests_qt/`: кнопка видимости по режиму/подключению, успешная загрузка
(фейковый worker) открывает Studio URL и пишет запись, повторная загрузка требует
подтверждения, ошибка показывает сообщение и не открывает Studio.

### B6. `docs:` пользовательская инструкция

Раздел в README (или `docs/YOUTUBE_UPLOAD.ru.md`) — как пользователю подготовить
Google Cloud:
1. Создать проект в Google Cloud Console, включить **YouTube Data API v3**.
2. OAuth consent screen: External; добавить себя в test users.
   **Важно:** в статусе *Testing* refresh-токены истекают через 7 дней — для
   постоянной работы перевести приложение в *In production* (для личного
   использования хватит экрана «Google hasn't verified this app» → «Continue»).
3. Credentials → OAuth client ID → тип **Desktop app** → скачать JSON →
   «Импортировать client_secret.json» в Settings.
4. Ограничения: дневная квота API (стоимость `videos.insert` смотреть в актуальной
   документации квот); private-блокировка до аудита
   (https://support.google.com/youtube/contact/yt_api_form); своя обложка требует
   подтверждённого телефоном канала.

## 5. Порядок коммитов (правило 8: один шаг = один коммит)

| # | Коммит | Фаза |
|---|---|---|
| 1 | `feat: youtube publish package model and validation` (A2) | A |
| 2 | `feat: expose edited youtube texts for publishing` (A3) | A |
| 3 | `feat: reveal_in_file_manager helper` (часть A4) | A |
| 4 | `feat: youtube publish dialog with handoff actions` (A4) | A |
| 5 | `feat: open publish dialog after youtube recipe run` (A5) | A |
| 6 | `docs: allow user-initiated youtube upload in offline-first rule` (B1) | B |
| 7 | `feat: youtube oauth loopback flow` (B2) | B |
| 8 | `feat: youtube resumable upload client` (B3) | B |
| 9 | `feat: youtube upload worker` (B4) | B |
| 10 | `feat: upload to youtube from publish dialog` (B5) | B |
| 11 | `docs: youtube upload setup guide` (B6) | B |

Перед **каждым** коммитом — полный pre-commit gate из `CLAUDE.md` (ruff, pytest
system python, compileall, mypy по блокирующему набору, `tests_qt` под
`.venv/bin/python` с `QT_QPA_PLATFORM=offscreen`). Коммитить только по разрешению
пользователя, если оно не дано заранее.

## 6. Чего НЕ делать

- Не автоматизировать браузер (Playwright/Selenium) в Studio.
- Не публиковать видео как `public` автоматически и не делать загрузку без нажатия
  кнопки пользователем.
- Не класть токены в `config.json`, логи, URL или сообщения об ошибках (логировать
  только статус-коды и `reason`).
- Не добавлять загрузку как шаг `JobEngine` и не трогать кэш-логику шагов.
- Не делать параллельных загрузок (одна активная загрузка на приложение).
- Не тестировать на реальном API в автотестах; ручная проверка — только с
  разрешения пользователя и на его аккаунте.

## 7. Ручная проверка (с пользователем)

1. Фаза A: прогон рецепта на коротком видео (~1 мин) → диалог → копирование →
   Studio → вставка → таймкоды распознаны YouTube как главы.
2. Фаза B: подключение аккаунта → загрузка того же видео как private → открылась
   страница редактирования в Studio, название/описание/теги/обложка на месте.
3. Отмена посреди загрузки; закрытие приложения посреди загрузки → при следующем
   открытии предложение продолжить.
4. Отключение аккаунта → токен удалён из Keychain.
5. Повторная загрузка той же записи → предупреждение о дубле.
