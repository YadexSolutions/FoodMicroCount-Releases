# Security Policy

## Supported releases

| Version | Status |
| --- | --- |
| v1.0.0-rc.1 | Public evaluation; security issues reviewed as resources permit |
| Older builds / unreleased snapshots | No guaranteed security maintenance |

This software is currently a release candidate. The project does **not** promise a fixed security response or remediation timeframe.

## Privately report a vulnerability

**Please do not publish exploit details, credentials, private data or a proof of concept in a public GitHub Issue or Discussion.**

If enabled, submit a report through the repository's [private vulnerability reporting form](https://github.com/YadexSolutions/FoodMicroCount-Releases/security/advisories/new). The repository maintainer should first enable GitHub's **Private vulnerability reporting** setting.

If GitHub does not offer that private form, use a private professional contact channel listed on the [maintainer's GitHub profile](https://github.com/YadexSolutions). If none is available, open a short, non-sensitive issue requesting a private reporting method, without posting the vulnerability details.

A useful private report includes:

1. A concise description of the vulnerability and potential impact.
2. The exact FoodMicro Count version and operating system.
3. Steps to reproduce and relevant logs, with secrets and personal data removed.
4. Any suggested mitigation or fix.

Please allow reasonable time for investigation before public disclosure.

## Data and privacy considerations

The core counting, saving and export functions run offline. Optional AI Assist may transmit user-selected crops only after explicit review and approval. Never submit sensitive laboratory images through optional external services unless you are authorised to do so.

## Scientific software note

A scientific or counting-method disagreement is generally **not** a security vulnerability. Report ordinary count errors, calculation defects and method-specific validation concerns through [Issues](https://github.com/YadexSolutions/FoodMicroCount-Releases/issues) or [Discussions](https://github.com/YadexSolutions/FoodMicroCount-Releases/discussions) without publishing confidential records.
