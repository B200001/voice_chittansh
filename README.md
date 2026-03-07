# Chittansh Voice System 

A LiveKit-based voice agent for sales calls, powered by Google Gemini.

## Prerequisites

- Python 3.9+
- [LiveKit Cloud](https://cloud.livekit.io) account (or self-hosted LiveKit server)
- Google Cloud project with Vertex AI enabled
- Google service account JSON credentials

## Setup

### 1. Clone and navigate to the project

```bash
cd chittansh-voice-system
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root (copy from `.env.example` if available) with:

| Variable | Description |
|----------|-------------|
| `LIVEKIT_URL` | LiveKit WebSocket URL (e.g. `wss://your-project.livekit.cloud`) |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `GOOGLE_API_KEY` | Google API key (for Gemini) |
| `GOOGLE_CLOUD_PROJECT` | Your Google Cloud project ID |
| `GOOGLE_CLOUD_LOCATION` | Region (e.g. `global`) |
| `GOOGLE_GENAI_USE_VERTEXAI` | Set to `True` for Vertex AI |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to your Google service account JSON file |

**Optional** (for pricing in the agent prompt):

- `COURSE_PRICE_FULL`, `COURSE_PRICE_DISCOUNTED`, `INSTALLMENT_1`, `INSTALLMENT_2`, `INSTALLMENT_3`

## Run

### Development mode (with auto-reload)

```bash
python src/agent.py dev
```

### Production mode

```bash
python src/agent.py start
```

### Local testing (console mode)

To test without connecting to a LiveKit room:

```bash
python src/agent.py console
```

Ensure your LiveKit server is running and the `.env` credentials are correct. For local development, you can use LiveKit Cloud or run [livekit-server](https://github.com/livekit/livekit) locally with `LIVEKIT_URL=wss://127.0.0.1:7880`.
