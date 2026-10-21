GFI - Generate Frame Inbetween
=============================

Make any video look smoother, on your own PC.

GFI creates new frames between the existing ones and saves a smoother copy. It
works with normal footage, AI-generated clips, animation, game recordings, and
old video. Everything runs locally; your files are never uploaded.

START HERE
----------
1. Keep this whole folder together. Extract it somewhere writable such as
   Desktop, Documents, or a USB drive. Do not put it in Program Files.
2. Double-click gfi.exe.
3. Drop a video onto the window, choose a style and frame rate, then click
   "Start interpolation".
4. When the preview appears, compare the two sides. Click "Save video..." to
   keep the result.

If Windows shows "Unknown publisher", choose "More info", then "Run anyway".
This build is not code-signed yet. If the zip came from the internet,
right-click the zip, open Properties, tick Unblock, then extract it.

WHAT YOU NEED
-------------
- Windows 10 or 11 on a 64-bit PC.
- A graphics card that supports DirectML (most recent NVIDIA, AMD, and Intel
  GPUs). CPU processing works, but it is much slower.
- No Python, FFmpeg, model downloads, installer, account, or admin rights.

YOUR FILES
----------
GFI stores its settings, temporary drafts, and preview cache in the gfi-data
folder next to gfi.exe. If you close GFI without saving, choose Discard to
remove the temporary draft. To remove GFI and its data, close the app and delete
this whole folder. Videos you save are normal files and are never deleted by
GFI.

UPDATING
--------
Replace gfi.exe and the _internal folder with the new release. Keep gfi-data to
keep your settings.

OPEN SOURCE AND LICENSES
------------------------
Source code: https://github.com/Leiifa/GFI
Releases:    https://github.com/Leiifa/GFI/releases

GFI's own code is MIT-licensed. Bundled third-party components and their
licenses are listed in THIRD_PARTY_NOTICES.md and the licenses folder.
