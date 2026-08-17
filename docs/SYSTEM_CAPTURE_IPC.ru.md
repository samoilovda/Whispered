# System Capture Helper: IPC contract v1

Статус: L8. Реализован вариант **маленький Swift helper на ScreenCaptureKit +
локальный Unix socket**. Этот документ фиксирует границу между helper и
Python-приложением; реализация `SystemAudioSource` и UI относятся к L9/L15.

## Почему этот вариант

ScreenCaptureKit умеет ограничивать content filter конкретным приложением или
окном и выдавать audio sample buffers. Конфигурация явно включает audio,
задаёт sample rate/channel count и может исключить собственный playback
Whispered. Helper не знает о Qt, VAD, ASR, истории или экспорте.

Поток данных:

```text
Whispered Python --Unix socket--> Swift helper --ScreenCaptureKit--> app/window audio
       ^                                  |
       +-------- framed PCM + status -----+
```

Helper также поддерживает read-only режим `--list-targets`. Он выводит
JSON-массив доступных приложений ScreenCaptureKit с `display_name`,
`bundle_id` и `process_id`, не создаёт `SCStream` и не начинает захват. UI
выполняет этот вызов в worker-потоке и только затем передаёт выбранную цель
обычному START-сообщению.

## Wire format

Каждый frame:

```text
magic[4] = "WSCA"
header_length[uint32 big-endian]
payload_length[uint32 big-endian]
header[UTF-8 JSON]
payload[bytes]
```

JSON header обязан содержать:

```json
{
  "protocol": "whispered.system-audio",
  "version": 1,
  "type": "audio"
}
```

Control frames имеют пустой payload. `audio` несёт signed little-endian PCM и
добавляет `sequence`, `source_timestamp`, `monotonic_timestamp`, `sample_rate`,
`channels`, `sample_format: "s16le"`, `payload_bytes`. Python decoder принимает
fragmented socket reads и несколько frames за один read; header ограничен 16
KiB, payload — 1 MiB.

## Message lifecycle

```text
helper -> hello
app    -> start {target: bundle_id | process_id | window_id}
helper -> started
helper -> meter / audio*
app    -> stop
helper -> stopped
```

`error {message, code?}` переводит session в failed. После `stop`/`stopped`
audio запрещён. Повторный Start создаёт новую session, а не переиспользует
старый stream. `Stop` должен вызвать `SCStream.stopCapture` и закрыть socket;
Python считает helper освобождённым только после `stopped` или подтверждённого
process exit.

## Аутентификация handshake

Каталог сокета создаётся с правами `0700`, сам socket-файл — с `0600` после
`bind()`, а его имя случайно генерируется на каждый запуск — это закрывает
межпользовательский доступ, но не защищает от другого процесса **того же**
пользователя, который мог бы выиграть гонку за первое `accept()` на сокете
раньше настоящего helper'а.

Поэтому перед `start`/`started` выполняется nonce handshake:

1. Python генерирует `secrets.token_hex(32)` (256 бит) на каждый `start()` и
   передаёт его helper-процессу через переменную окружения
   `WHISPERED_CAPTURE_NONCE` — не через argv, который виден в `ps` другим
   процессам того же пользователя.
2. Helper эхом добавляет это значение в поле `nonce` своего `hello` frame.
3. Python сверяет `nonce` из `hello` с тем, что сам сгенерировал. Несовпадение
   или отсутствие поля — это `ProtocolError`; `start` не отправляется, PCM не
   принимается, соединение и helper-процесс закрываются.

Реализация: `core/live/system_capture_protocol.py::generate_nonce`/
`hello_frame`, `core/live/system_audio_source.py::_launch_helper`/`_consume`,
`native/system_capture_helper/.../main.swift` (читает
`WHISPERED_CAPTURE_NONCE` и кладёт в `hello`).

**Не реализовано (известный gap, см. `docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md`,
R4):** проверка peer credentials на macOS (`LOCAL_PEERCRED`/`LOCAL_PEERPID`)
как дополнительный слой поверх nonce handshake.

## Compatibility rules

- неизвестная `version` отклоняется с понятной ошибкой;
- новые поля header можно добавлять, старые поля не переименовывать;
- неизвестный `type` отклоняется;
- audio sequence и оба timestamp обязательны;
- helper не отправляет собственный Whispered playback;
- более двух удалённых участников остаются общим `Meeting audio` mix.

Swift helper находится в `native/system_capture_helper`; release-сборка и
реальный HELLO handshake проверены 18 июля 2026. Python reference
implementation и tests находятся в
`core/live/system_capture_protocol.py`,
`core/live/system_audio_source.py` и
`tests/test_system_capture_protocol.py` /
`tests/test_system_audio_source.py`. Реальный ScreenCaptureKit capture,
permissions и выбор Zoom/Meet/Teams остаются ручной приёмкой L8/S1/L9.
