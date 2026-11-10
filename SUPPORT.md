# FoodMicro Count — Help and Support

FoodMicro Count is open-source desktop software currently available as a **Windows x64 evaluation release candidate**.

## Start here

1. Read the [project README](README.md) for supported Windows versions, the main workflow and scientific limitations.
2. Download the latest appropriate file from [GitHub Releases](https://github.com/YadexSolutions/FoodMicroCount-Releases/releases). For the current evaluation version, see [v1.0.0-rc.1](https://github.com/YadexSolutions/FoodMicroCount-Releases/releases/tag/v1.0.0-rc.1).
3. Follow [WINDOWS_READ_ME_FIRST.txt](WINDOWS_READ_ME_FIRST.txt) and the [Beginner's Guide](docs/FoodMicroCount-1.0-Beginners-Guide.pdf).
4. Check the installer SHA-256 checksum against the published checksum before running the unsigned installer.
5. Try `demo/synthetic_plates.png` before using your own plates.

## Where to ask for help

| Need | Where to go |
| --- | --- |
| Installation or use | [Discussions — questions](https://github.com/YadexSolutions/FoodMicroCount-Releases/discussions) |
| How to interpret a software feature | [Discussions](https://github.com/YadexSolutions/FoodMicroCount-Releases/discussions) |
| Feature proposal or workflow idea | [Discussions](https://github.com/YadexSolutions/FoodMicroCount-Releases/discussions) |
| Reproducible software bug | [Issues](https://github.com/YadexSolutions/FoodMicroCount-Releases/issues) |
| Potential security vulnerability | [SECURITY.md](SECURITY.md) — use private reporting |
| Code or documentation contribution | [CONTRIBUTING.md](CONTRIBUTING.md) |

## What to include in a support request

Provide the software version, Windows version, what you were trying to do, the steps taken, what you expected, what happened and any non-sensitive screenshot or error message. For colony-counting questions, include the test/medium and counting mode if relevant.

Do not publish patient, client or laboratory identifiers, confidential research images, personal information or proprietary records. Use public synthetic examples when practical.

## Important evaluation limits

The current installation package is **unsigned** and bundles its runtime. An unsigned executable may be flagged by Windows security checks: verify the origin and published SHA-256 checksum rather than ignoring warnings without investigation.

Automatic detections are candidate colonies and must be checked against the original image. Independent laboratory validation and clean-machine acceptance testing are pending. FoodMicro Count does not establish organism identity, replace method verification or confer laboratory accreditation.

## Data storage and upgrades

By default, application records live under `%LOCALAPPDATA%\FoodMicroCount`. Use the application's **Backup** function before upgrading. See [release notes](docs/RELEASE_NOTES.md) for version-specific details.

Community support is provided on a best-effort basis; a service-level response time is not guaranteed.
