# Third-party notices

SwarLekhak 0.1.0 (hackathon prototype). Bundled or required components and their licences as declared by
their projects at the time of build. Re-verify before any redistribution.

| Component | Version | Licence | Use |
|---|---|---|---|
| Python | 3.11 | PSF | runtime |
| PySide6 / Qt for Python | 6.12.0 | LGPL-3.0 (Qt open source) | native UI, tray, overlay |
| Vosk API | 0.3.45 | Apache-2.0 | local speech recognition |
| vosk-model-small-en-us-0.15 | 0.15 | Apache-2.0 (as listed on the Vosk models page) | commands, wake approximation, verification hypothesis |
| NumPy | 2.4.6 | BSD-3-Clause | resampling, sonification |
| sounddevice / PortAudio | 0.5.6 | MIT | microphone capture and playback |
| cryptography | 50.0.2 | Apache-2.0 / BSD | AES-GCM, X25519, HKDF |
| pywin32 | 312 | PSF-style | SAPI TTS, DPAPI |
| python-docx | 1.2.0 | MIT | Word export |
| psutil | 7.2.2 | BSD-3-Clause | Wispr Flow process detection (heuristic) |
| PyInstaller (build only) | 6.22.3 | GPL-2.0 with bootloader exception | packaging |

Google Gemini API text-to-speech is an optional, user-enabled cloud service accessed with the user's own API key.
ElevenLabs text-to-speech is an optional, user-enabled cloud service accessed with the user's own API key;
voice "Abhi" is from the ElevenLabs Voice Library and is subject to ElevenLabs' terms and the user's plan.

Wispr Flow is a separate commercial product. It is **not** bundled or redistributed; users install and
license it themselves from wisprflow.ai. SwarLekhak is not affiliated with or endorsed by Wispr.

Microsoft SAPI voices (David, Zira) are part of Windows; synthetic demo fixtures were generated with them.
