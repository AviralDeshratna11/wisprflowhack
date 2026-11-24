# SwarLekhak privacy notice (prototype)

**What SwarLekhak records.** Only *dictation turns* you start with "start writing" are recorded, as
evidence that your written answer matches your own speech. Each turn's audio is encrypted (AES-256-GCM)
on this computer with a key protected by Windows DPAPI for your Windows account.

**What it never records.** Passive wake-phrase listening is processed in memory by a local speech model and
is never written to disk or to logs. Passive listening is **off** until you consent in Setup, and "mute"
releases the microphone completely. Diagnostic logs never contain what you said or wrote.

**Wispr Flow.** Your answers are written by Wispr Flow, a separate product you install yourself. Flow may
send your audio to its own servers and needs an internet connection; that processing is governed by Wispr's
own terms and privacy policy, not by SwarLekhak.

**Assistant voice (Gemini TTS, opt-in).** If you choose the Gemini voice and tick its consent box, every sentence
SwarLekhak speaks - including question text and read-backs of your answers - is sent to Google's Gemini API to be
synthesised, under Google's terms. The key is stored encrypted with Windows DPAPI. Gemini *Live* (a conversational
model that writes its own replies) is not used.

**Assistant voice (ElevenLabs, opt-in).** If you choose the ElevenLabs voice ("Abhi") and tick the consent box
in Settings, every sentence SwarLekhak speaks - including question text and read-backs of your answers - is sent
to ElevenLabs over the internet to be synthesised, and ElevenLabs may log it under its own terms. Your API key is
stored encrypted with Windows DPAPI. Without consent, a key, or a connection, SwarLekhak uses the local Windows
voice and nothing is sent. Your microphone audio is never sent to ElevenLabs.

**Local processing.** Command recognition, verification (comparison of written text with a local
recognition of your recording), speech output (Windows SAPI) and storage are all on this computer.
SwarLekhak opens no network listener and has no server.

**Submission.** When you submit, a package is written to your SwarLekhak submissions folder. Audio in it is
encrypted for the examiner's public key from the exam package, so only the examiner can open it. You decide
how the package is delivered.

**Retention.** Local evidence audio of *submitted* examinations is deleted after the retention period in
Settings (default 30 days). Uninstalling asks whether to delete all SwarLekhak data.

**Review markers** compare two machine recognisers. They are neutral prompts for you to check; they are
not evidence of misconduct and are not identity verification.
