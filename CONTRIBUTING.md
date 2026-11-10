# Contributing to FoodMicro Count

Thank you for helping improve FoodMicro Count. Contributions from microbiologists, laboratory analysts, researchers, students and developers are welcome.

## Before contributing

FoodMicro Count v1.0.0 RC1 is **evaluation software**, not a validated replacement for an approved laboratory procedure. Automatic colony markers are candidates that need analyst review. See the [README](README.md), [validation scope](docs/VALIDATION.md), [release notes](docs/RELEASE_NOTES.md) and [GPL-3.0-or-later licence](LICENSE).

The public project for contributions and discussion is [YadexSolutions/FoodMicroCount-Releases](https://github.com/YadexSolutions/FoodMicroCount-Releases). Please use this public repository rather than assuming access to a separate private development repository.

## Choose the right place to contribute

- **Question or installation help:** ask in [Discussions](https://github.com/YadexSolutions/FoodMicroCount-Releases/discussions).
- **Feature proposal or scientific workflow idea:** start a discussion so the expected behaviour can be agreed before code is written.
- **Reproducible defect:** open an [Issue](https://github.com/YadexSolutions/FoodMicroCount-Releases/issues) with the current version, operating system, steps, expected result and actual result.
- **Code, tests or documentation:** fork the public repository, create a focused branch and submit a pull request.
- **Security issue:** follow [SECURITY.md](SECURITY.md); do not disclose exploit details in public issues.

## Reproducing a bug or evaluating detection

Where relevant, state the test and culture medium, colony appearance, image quality, plate boundary, counting mode, detection settings, manual reference count, software count and the difference observed. Include the sample preparation, dilution and plated volume only if needed to explain a calculation defect.

**Do not upload identifiable client records, confidential laboratory data, unpublished workbooks or biological sample images unless you have permission to share them publicly.** The included practice material is synthetic; it is not evidence of accuracy on real plates. If a public demonstration is needed, use permitted or synthetic examples.

## Development and tests

The reference Windows build uses x64 Python 3.12. The project has additional build-specific prerequisites; consult [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md) before building an installer. A simple local test workflow from the repository root is:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-build.txt
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:QT_QPA_PLATFORM
```

If `py` is unavailable, use the installed Python 3.12 executable. Run relevant tests and document any failing tests in your pull request. Avoid committing generated build output, local user records or confidential data.

## Pull request checklist

- Explain the change, motivation and affected user workflow.
- Link to any relevant Issue or Discussion.
- Include or update tests for behaviour changes.
- Update documentation and release notes when user-facing behaviour changes.
- Preserve original observations, analyst edits, audit history, qualifiers and traceability.
- Do not silently change method-specific counting limits or claim method validation without supporting evidence.
- Confirm that new image or dataset examples are shareable, properly attributed and licensed for inclusion.
- Keep changes focused and explain any third-party code or licence implications.

All accepted source contributions are intended for distribution under the repository's **GPL-3.0-or-later** licence. If your contribution has different ownership or licensing constraints, discuss them before submitting it.

## Scientific proposals

A proposal involving media-specific colony morphology, spreaders, overlapping colonies, dilutions, confirmation steps or report calculations should state the intended method and its limits. The project maintainer may request supporting source references, independent plate annotations and verification evidence. A proposed feature is not considered a validated method merely because it works on synthetic images.

Thank you for contributing to practical, transparent microbiological analysis tools.
