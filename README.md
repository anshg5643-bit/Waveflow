# WaveFlow — Audio Steganography Prototype

WaveFlow is a full-stack prototype for hiding an encrypted text message or file inside a WAV audio file, then extracting it with the correct password. It also includes an audio analysis feature, operation history, optional Google sign-in, and an optional Groq-powered chatbot.

> **Project status:** Hackathon / educational prototype. It is not a certified security product. Test with non-sensitive sample data only.

## Features

- **Encode:** encrypt a message or file with AES-256-GCM and embed it into WAV audio using phase-based signal processing.
- **Decode:** recover a hidden payload with the correct password.
- **Inspect:** view audio properties and estimated payload capacity.
- **Analyze:** run the included ML steganalysis detector for an indicative assessment.
- **History:** view and manage operations associated with the current account.
- **Authentication:** create an account with email/password, sign in with an existing account, optional Google Sign-In, and optional guest mode.
- **Assistant:** optional Groq chatbot integration.
- **Sample audio:** generate a WAV carrier to try the workflow.

## Technology stack

- **Backend:** Python, FastAPI, Uvicorn
- **Frontend:** HTML, CSS, vanilla JavaScript
- **Audio / ML:** NumPy, SciPy, scikit-learn, joblib
- **Encryption:** `cryptography` (AES-GCM and scrypt-based key derivation)
- **Database:** SQLAlchemy with SQLite by default
- **Optional integrations:** Google Identity Services and Groq API
- **Deployment:** Docker and Docker Compose

## Requirements

- Python 3.11 or 3.12 recommended
- pip
- Optional: Docker / Docker Compose
- Optional: Google OAuth client ID and Groq API key for those features

## Run on Windows

1. Extract the ZIP and open a terminal in the `waveflow` project folder.
2. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install dependencies:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. (Optional) Configure credentials using either `.env` (copy `.env.example`) or `api_keys.txt` in the project root. The app can be used in guest mode if enabled. The provided `api_keys.txt` contains placeholders; replace only the values for services you use. Keep real keys private.
5. Start the server using either command:

   ```powershell
   python -m uvicorn app:app --reload
   ```

   or

   ```powershell
   python -m uvicorn backend.main:app --reload
   ```

6. Open [http://127.0.0.1:8000](http://127.0.0.1:8000). API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

You can also try `setup.bat` and `start.bat` if supplied for your Windows environment.

## Environment configuration

Create a `.env` file in the project root from `.env.example`. Common settings:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Enables the optional chatbot |
| `GROQ_MODEL` | Groq model name |
| `GOOGLE_CLIENT_ID` | Enables Google Sign-In for your OAuth web client |
| `JWT_SECRET` | Secret used to sign session tokens; set a long random value |
| `ALLOW_GUEST_LOGIN` | Allows guest access when `true` |
| `COOKIE_SECURE` | Set `true` when serving over HTTPS |
| `DATABASE_URL` | Optional database URL; SQLite is the default |
| `MAX_UPLOAD_MB` | Optional upload-size limit |
| `MAX_AUDIO_SECONDS` | Optional maximum audio duration |

Do not commit `.env`, API keys, passwords, or production secrets to source control. For deployment, use the hosting provider's environment-variable settings and HTTPS.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/config` | Public feature configuration |
| `GET` | `/api/sample` | Generate sample WAV audio |
| `POST` | `/api/inspect` | Inspect uploaded audio |
| `POST` | `/api/encode` | Hide encrypted message/file in audio |
| `POST` | `/api/decode` | Extract hidden payload |
| `POST` | `/api/analyze` | Run the detector |
| `GET` | `/api/history` | List current user's operations |
| `DELETE` | `/api/history/{op_id}` | Delete an operation |
| `GET` | `/api/files/{op_id}` | Access an operation's output file |
| `POST` | `/api/auth/register` | Create an email/password account |
| `POST` | `/api/auth/login` | Sign in with email/password |
| `/api/auth/*` | — | Google, guest, session, and logout routes |
| `/api/chat/*` | — | Optional chatbot and chat history routes |

For full request and response schemas, run the app and visit `/docs`.

## Tests and model

Run the test suite from the project root:

```powershell
python -m pytest -q
```

The repository includes `train_detector.py` to retrain the detector when the required dependencies and training setup are available. The existing detector model is stored under `backend/models/`. Model scores should be treated as indicative, not definitive, especially on real-world recordings.

## Project structure

```text
waveflow/
├── app.py                 # Root ASGI entry point (compatibility wrapper)
├── backend/
│   ├── main.py             # FastAPI app and API routes
│   ├── auth.py             # Authentication routes
│   ├── chat.py             # Optional chatbot routes
│   ├── config.py           # Environment configuration
│   ├── crypto.py           # Encryption helpers
│   ├── database.py         # Database models and setup
│   ├── detector.py         # ML-based audio analysis
│   ├── stego.py            # Audio hiding/extraction engine
│   ├── synth.py            # Sample carrier audio generation
│   └── models/             # Detector model
├── frontend/               # HTML, CSS, JavaScript
├── tests/                  # Automated tests
├── requirements.txt
├── .env.example
├── api_keys.txt           # Local secret template; ignored by Git
├── Dockerfile
└── docker-compose.yml
```

## Important limitations

- Use WAV files supported by the app. Converting, trimming, resampling, or lossy-compressing the encoded audio may destroy the hidden payload.
- Detector output is a model estimate and is not proof that audio does or does not contain hidden data.
- Google Sign-In and the chatbot require their respective credentials. Use `.env` or the ignored local `api_keys.txt`; never commit real secrets.
- This prototype has not undergone an independent security audit. Do not use it to protect high-value or highly sensitive information.

---

**WaveFlow — Hackathon prototype by Dig Busters**
