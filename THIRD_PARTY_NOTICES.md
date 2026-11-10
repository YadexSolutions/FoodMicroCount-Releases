# Licence and third-party notices

FoodMicro Count application code, tests, documentation and synthetic fixtures are distributed under GPL-3.0-or-later. See [GNU GPL version 3](LICENSE). Copyright and developer attribution are retained. The corresponding application source and build scripts are available with the Windows binary in the release. The installer includes a copy of that source.

## Qt and Qt for Python

This application uses Qt 6.12.0 and Qt for Python 6.12.0 (PySide6 Essentials and Shiboken), copyright The Qt Company Ltd. and other contributors. The applicable library components are used under LGPLv3. The installed `Third-party licences` folder contains the LGPL text, the upstream Qt attribution files and notices from the wheels. The GNU GPL text is installed as LICENSE. See [Qt LGPL terms](https://doc.qt.io/qt-6/lgpl.html) and [Qt for Python licences](https://doc.qt.io/qtforpython-6/licenses.html).

Matching upstream library source is available without charge:

- [Qt Base 6.12.0 source](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtbase-everywhere-src-6.12.0.tar.xz) for the Core, GUI, Network and Widgets libraries and their plugins.
- [Qt SVG 6.12.0 source](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtsvg-everywhere-src-6.12.0.tar.xz) for the SVG library and plugins.
- [Qt Image Formats 6.12.0 source](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtimageformats-everywhere-src-6.12.0.tar.xz) for additional image plugins.
- [Qt for Python 6.12.0 source, including Shiboken](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz).

FoodMicro Count uses the upstream library binaries without source modifications. The Qt DLLs, bindings and plugins are separate files under the installed `_internal` directory. Users may replace them with ABI-compatible modified builds and debug those modifications. The build instructions also support rebuilding the application with replacement libraries.

## Other components

| Component | Licence and upstream information |
| --- | --- |
| CPython | [PSF licence and bundled notices](https://docs.python.org/3/license.html) |
| NumPy and SciPy | BSD-3-Clause and bundled numerical-library notices. [NumPy](https://numpy.org/doc/stable/license.html), [SciPy](https://github.com/scipy/scipy/blob/main/LICENSE.txt), [OpenBLAS](https://github.com/OpenMathLib/OpenBLAS) |
| Pillow | HPND-style PIL/Pillow licence and bundled image-library notices. [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| OpenCV and opencv-python-headless | Apache-2.0 for OpenCV and applicable wheel notices. [OpenCV](https://github.com/opencv/opencv/blob/5.0.0/LICENSE), [Windows wheel packaging](https://github.com/opencv/opencv-python) |
| scikit-image | [BSD-3-Clause and included notices](https://github.com/scikit-image/scikit-image/blob/main/LICENSE.txt) |
| openpyxl | [MIT](https://openpyxl.readthedocs.io/) |
| PyInstaller | [GPL with a distribution exception for generated bundles](https://pyinstaller.org/en/stable/license.html) |
| Inno Setup | [Setup engine and compiler terms](https://jrsoftware.org/isinfo.php) |

Transitive package notices, the CPython licence and the exact build environment inventory are shipped with the installer. No OpenCFU code, third-party model weights, laboratory records, real plate photographs or proprietary workbook samples are included. The application data catalogue links to manufacturer references. Those references retain their original terms.
