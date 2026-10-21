# Changelog

## 0.1.0 - first public release

- Portable Windows build: no Python, FFmpeg, model download, installer, or admin
  rights required.
- 10-bit SDR and HDR10/HLG pipeline with automatic HEVC selection.
- Live-action and Anime/Cartoon RIFE profiles.
- Permanent two-pane workspace with a paused comparison preview and one progress
  display.
- Temporary render drafts with asynchronous **Save video…**, `Ctrl+S`, and
  unsaved-draft prompts.
- Self-contained data folder next to `gfi.exe` for settings, drafts, and preview
  cache.
- CLI remains available from source checkouts.

### Known limitations

- Unsigned build; Windows SmartScreen may show an unknown-publisher warning.
- HDR10 mastering-display and Dolby Vision dynamic metadata are not copied.
- 4K on 8 GB VRAM is slow.
- VFR sources are normalized to CFR using the average frame rate.
- The preview is a 60 fps SDR proxy, not a color-managed HDR preview.
