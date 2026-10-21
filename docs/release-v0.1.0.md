# GFI 0.1.0 — Portable Windows

**Make any video look smoother — on your own PC.**

GFI creates new frames between the frames already in a video and saves a
smoother copy. Use it for AI-generated clips, animation, game recordings, or old
footage. Everything runs locally on your graphics card; your videos are never
uploaded.

[**Download GFI 0.1.0 for Windows**](https://github.com/Leiifa/GFI/releases/download/v0.1.0/GFI-0.1.0-win64.zip)

![GFI desktop window](https://raw.githubusercontent.com/Leiifa/GFI/main/docs/gui.png)

## Start here

1. Download `GFI-0.1.0-win64.zip`.
2. Right-click the zip, choose **Properties**, tick **Unblock**, then extract it
   to a writable folder such as Desktop or Documents. Do not use Program Files.
3. Open the extracted folder and double-click **`gfi.exe`**.
4. Drop in a video, choose a style and frame rate, then click **Start
   interpolation**.
5. Compare the preview, then click **Save video...** to keep the result. If you
   do not want it, close GFI and choose **Discard**.

No installer, Python, FFmpeg, model download, account, or administrator rights
are needed. The app, both models, and FFmpeg are inside the folder.

## What you need

- Windows 10 or 11 on a 64-bit PC.
- A graphics card that supports DirectML (most recent NVIDIA, AMD, and Intel
  GPUs). CPU processing works but is much slower.
- Enough free space for the app (under 1 GB unpacked) and your output video.

## Good to know

- **SmartScreen:** This first build is not code-signed, so Windows may show an
  **Unknown publisher** warning. Choose **More info → Run anyway**.
- **Your files stay local:** Settings, drafts, and preview cache live in
  `gfi-data\` next to `gfi.exe`. Delete the whole folder to remove the app and
  its data. Saved videos are normal files and are not deleted by GFI.
- **HDR:** HDR10 and HLG are preserved by default. HDR10 mastering-display
  metadata and Dolby Vision dynamic metadata are not copied.
- **Preview:** The comparison preview is an SDR proxy. The saved video is
  encoded at full quality and keeps its HDR tags.

## What's included

- GFI 0.1.0 portable desktop app (`gfi.exe`).
- Live-action and Anime/Cartoon RIFE models.
- Gyan FFmpeg 9.0.2 full build, including `zscale`, `libx265`, and AMD hardware
  encoders.
- `START HERE.txt`, license files, and third-party notices.

## Verify the download

Download `SHA256SUMS.txt` from the assets below and compare it with:

```powershell
Get-FileHash .\GFI-0.1.0-win64.zip -Algorithm SHA256
```

## Open source

GFI's own code is MIT-licensed. The source, build script, and issue tracker are
at [github.com/Leiifa/GFI](https://github.com/Leiifa/GFI). Bundled third-party
components and their licenses are listed in `THIRD_PARTY_NOTICES.md` and the
`licenses` folder.
