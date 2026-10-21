GFI - Generate Frame Inbetween
Portable edition

Running GFI
-----------
1. Extract this folder somewhere writable, such as Desktop, Documents, or a USB drive.
   Do not run it from Program Files; GFI needs to write its settings and drafts next to gfi.exe.
2. If Windows shows an "Unknown publisher" warning, choose More info, then Run anyway.
3. Start gfi.exe.

If the zip came from the internet, right-click the zip, open Properties, tick Unblock,
then extract. This avoids some first-run warnings.

No Python, FFmpeg, model download, or administrator rights are needed. The models and
FFmpeg are included in _internal.

GFI stores its settings, drafts, and preview cache in the gfi-data folder next to
gfi.exe. Closing GFI removes temporary drafts. Delete the whole folder to remove the
application and its data.

Updating
--------
Replace gfi.exe and the _internal folder with the new release. Keep gfi-data to retain
your settings.

Open source
-----------
The source code is available with the release. GFI's own code is MIT-licensed. Bundled
third-party components and their licenses are listed in THIRD_PARTY_NOTICES.md and the
licenses folder.

Requirements
------------
Windows 10 or 11, a DirectML-capable GPU (NVIDIA, AMD, or Intel), and a recent graphics
driver. CPU inference is available but slower.
