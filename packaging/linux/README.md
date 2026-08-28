# Linux packaging

Fedora-oriented, mic-only preview channel. Same PyInstaller pipeline as
macOS/Windows; output is a relocatable one-directory tree plus a
`.tar.gz`.

## Build

```bash
sudo dnf install -y python3.11 python3.11-devel gcc gcc-c++ cmake make \
    libxkbcommon-x11 mesa-libEGL mesa-libGL dbus-libs
./packaging/linux/build-linux.sh
```

`pywhispercpp` is compiled from source (CPU). For a GPU build export
`GGML_CUDA=1` or `GGML_VULKAN=1` before running the script.

Build inside a container matching the oldest distribution you want to
support — a binary linked against a newer glibc will not start on older
systems. The release workflow builds in a current Fedora image.

## Run

```bash
tar xzf dist/Whispered-linux-x86_64.tar.gz
./Whispered/Whispered
```

Runtime needs `ffmpeg` (`dnf install ffmpeg-free`) for media workflows and
`xcb-util-cursor` for the Qt `xcb` platform plugin.

## Not yet included

- System-audio capture (macOS-only ScreenCaptureKit helper) — Live runs
  microphone-only on Linux; preflight reports this.
- RPM / Flatpak packaging and a reproducible `requirements-linux.lock`.
- `appimage/build-appimage.sh` is an older, separate experiment and is
  not a validated release channel.
