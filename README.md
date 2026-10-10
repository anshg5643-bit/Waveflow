WaveFlow — Hide Encrypted Data in Sound

By Team Dig Busters

What is WaveFlow?

WaveFlow is a private data transmission tool that hides secret messages or files inside ordinary audio files. The audio sounds completely normal to anyone who listens — but the right password reveals the hidden data inside.

Think of it as an invisible envelope inside a song.

The Problem It Solves

Traditional methods of hiding data have obvious weaknesses:

Firewalls can spot and block suspicious transfers
Encryption looks suspicious — someone clearly has something to hide
Steganography tools leave obvious audio distortion

WaveFlow combines both encryption AND hiding, so:

The file looks like a normal WAV audio file
There is no obvious digital trail
A listener hears nothing unusual
How It Works
Your Secret Message
       ↓
AES-256-GCM Encryption  ← protected by your password
       ↓
FFT Phase Modulation    ← hidden in the sound frequencies (300Hz–6kHz)
       ↓
Normal-sounding WAV file  ← share it anywhere

Two layers of protection:

Layer 1 — Encrypt: Your message is locked with AES-256-GCM military-grade encryption. Without the password, it’s unreadable garbage.
Layer 2 — Hide: The encrypted data is tucked into the phase of the audio’s frequency spectrum — the part your ears can’t detect. Only the frequency positions defined by your password are used.
Key Features
Feature	Description
🔐 AES-256-GCM Encryption	Military-grade encryption before hiding
🎵 Phase Modulation	Hides data in FFT phase, not amplitude — audio sounds identical
🔑 Password-Keyed Positions	Which frequency bins hold the data depends on your password
📁 Text + File Support	Hide messages or entire files (up to 5 MB)
🤖 AI Chatbot	Groq-powered assistant explains everything in plain English
🔍 Stealth Analyzer	ML model checks if a file would be flagged by a detector
👤 Google Sign-In	Secure login with your Google account
📊 History	Download your previously hidden audio files anytime
Tech Stack
Layer	Technology
Backend	FastAPI + Python
Database	SQLite / PostgreSQL
Crypto	AES-256-GCM + scrypt
Signal Processing	NumPy + SciPy (FFT)
ML Detector	scikit-learn Gradient Boosting
AI Chatbot	Groq API (Llama 3.3 70B)
Auth	Google Sign-In + JWT
Frontend	Vanilla JS/CSS (no framework)
Real-World Use Cases
🕵️ Journalists hiding sources inside music files
🏢 Enterprises sending confidential data without triggering network alerts
🔬 Researchers watermarking audio datasets
👨‍💻 Security professionals testing steganographic detection systems
