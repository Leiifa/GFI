# Third-party notices

GFI's own source code is licensed under the MIT License in `LICENSE`.

The portable release bundles the following components. Their licenses apply to those
components, not to GFI's own source code.

## FFmpeg 9.0.2

- Component: `ffmpeg.exe` and `ffprobe.exe` from the Gyan.dev Windows full build
  (`9.0.2-full_build-www.gyan.dev`).
- License: GNU General Public License v3.
- Full license: `licenses/FFmpeg-LICENSE.txt`.
- Build information: `licenses/FFmpeg-README.txt`.
- Source code: https://github.com/FFmpeg/FFmpeg/commit/946fcce07b
- Build source: https://github.com/GyanD/codexffmpeg
- GFI invokes FFmpeg as a separate process through pipes.

## PySide6 and Qt 6.12.0

- Components: PySide6 and the Qt 6 libraries used by the desktop window.
- License: LGPL-3.0-only, GPL-2.0-only, or GPL-3.0-only (GFI uses the LGPL terms).
- Full LGPL text: `licenses/LGPL-3.0.txt`.
- Project: https://pyside.org and https://www.qt.io/
- The Qt DLLs are shipped as replaceable files inside `_internal` so the library can
  be relinked or replaced as allowed by the LGPL.

## ONNX Runtime 1.24.4

- Component: ONNX Runtime DirectML execution provider and its runtime DLLs.
- License: MIT.
- Copyright: Microsoft Corporation.
- Full text: `licenses/MIT.txt` (also covers the RIFE components below).
- Project: https://github.com/microsoft/onnxruntime

## Microsoft DirectML

- Component: `DirectML.dll`, redistributed with ONNX Runtime.
- License: Microsoft DirectML redistributable terms.
- Project: https://learn.microsoft.com/windows/ai/directml/

## RIFE and ONNX models

- Component: RIFE interpolation engine and the two bundled ONNX exports.
- License: MIT.
- Full text: `licenses/MIT.txt`.
- Sources:
  - https://github.com/hzwer/Practical-RIFE
  - https://huggingface.co/walterlow/RIFE_fp32_timestep
  - https://huggingface.co/edgetools/rife

## NumPy 2.5.4

- License: BSD-3-Clause and additional component licenses.
- Full texts: `licenses/numpy/`.
- Project: https://numpy.org/

## Python 3.12

- Component: Python runtime bundled by PyInstaller.
- License: Python Software Foundation License.
- Full text: `licenses/Python-LICENSE.txt`.
- Project: https://www.python.org/

## PyInstaller

- Component: application bootloader.
- License: GPL-2.0 with the PyInstaller bootloader exception.
- Full text: `licenses/PyInstaller-COPYING.txt`.
- Project: https://pyinstaller.org/

## Microsoft Visual C++ runtime

- Component: runtime DLLs required by PySide6, ONNX Runtime, and Python.
- License: Microsoft Visual C++ redistributable terms.
- Project: https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist
