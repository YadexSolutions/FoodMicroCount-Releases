# FoodMicro Count

**Windows desktop software for analyst-reviewed microbial colony counting.**  
Developed by Yaw Agyei Gyasi.

FoodMicro Count helps you import plate photographs, count visible candidates, review and correct markers, and document quantitative calculations. The initial Windows distribution is **1.0.0 RC 1**. The app displays **Version 1.0**, and its executable and Python package identify as **1.0.0**. The release tag identifies the candidate packaging.

## Install on Windows

1. Open [GitHub Releases](https://github.com/YadexSolutions/FoodMicroCount-Releases/releases) and select **v1.0.0-rc.1**.
2. Download `FoodMicroCount-1.0.0-Windows-x64-Setup.exe` and its SHA-256 file. Compare the SHA-256 value with `Get-FileHash -Algorithm SHA256` in PowerShell before running Setup.
3. Run the unsigned, per-user installer on Windows 10 x64 (1809 or later) or Windows 11 x64. Setup includes Python and shows a Read Me and first-run instructions.
4. Open **FoodMicro Count** from Start. The [illustrated Beginner Guide](docs/FoodMicroCount-1.0-Beginners-Guide.pdf) is also available from the Start menu.

## What the app does

- Imports plate images and proposes circular or elliptical regions. Select and verify each dish independently.
- Supports Quick Count, Assisted Count and Manual Count, with zoom, full-screen inspection and an editable marker layer.
- Separates original automatic counts from analyst corrections. Supports undo, redo, splits, merges, uncertainty flags and observation states.
- Stores source images, settings, revisions and audit records locally. Imports Excel observations and exports Excel, CSV, JSON and annotated PNGs.
- Provides method-specific calculation traces, reporting qualifiers and editable culture-media presets.

## Try a practice count

Open `demo/synthetic_plates.png` from the installed program folder. Choose **Aerobic Plate Count** and **PCA**. Select a plate, check its outline, then choose **Run colony detection**. The practice dishes contain 44 and 18 generated objects. Review the markers and use the guide to enter the sample and dilution, check the calculation and export the result.

The app's essential workflows run offline. Optional AI Assist sends only the selected crops after approval. Use **Backup** before upgrading. Default records are in `%LOCALAPPDATA%\FoodMicroCount`, separate from the program folder.

## Scientific limits

Automatic markers are candidates. Review image scale, lighting, plate boundaries, artefacts, touching colonies, and the intended method before using a result. The included images and workbook examples are synthetic and support software checks. Representative laboratory validation and clean-machine acceptance testing are pending. Some calculation modes have explicit scope restrictions. The software does not establish organism identity or laboratory accreditation.

## Source and licences

Use the tag **v1.0.0-rc.1** or the matching `FoodMicroCount-1.0.0-Source.zip` attached to the release. Both describe the same source tree. See [build instructions](docs/BUILD_WINDOWS.md), [release notes](docs/RELEASE_NOTES.md), [validation scope](docs/VALIDATION.md), and [third-party notices](THIRD_PARTY_NOTICES.md). FoodMicro Count is [GPL-3.0-or-later](LICENSE).
