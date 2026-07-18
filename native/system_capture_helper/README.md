# Whispered ScreenCaptureKit helper

This macOS 13+ executable implements `docs/SYSTEM_CAPTURE_IPC.ru.md`. It owns
`SCStream`, captures audio for one selected application/window, excludes the
current helper process, and sends mono 16 kHz `s16le` frames over a local Unix
socket.

Build for development:

```bash
cd native/system_capture_helper
swift build -c release
```

The Python adapter launches
`.build/release/whispered-capture-helper` with
`WHISPERED_CAPTURE_SOCKET=<path>`. A production `.app` must copy and sign this
binary as part of L23; the development build is intentionally not committed.

List currently shareable applications as JSON without starting capture:

```bash
.build/release/whispered-capture-helper --list-targets
```

Each entry contains `display_name`, `bundle_id`, and `process_id`. Discovery
does not create an `SCStream`; the selected target is sent later in the normal
START control frame.

The first real capture triggers the standard macOS Screen Recording permission
flow. A successful `swift build` and HELLO handshake prove the native/IPC
boundary only; application capture and permission UX remain manual acceptance.
