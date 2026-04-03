# Code Narrator AI

A full-stack AI app that analyzes uploaded or pasted source code using a local Ollama model, then returns:

- A simple human-readable explanation
- A step-by-step breakdown
- A Mermaid flowchart diagram rendered in the browser

## Project Structure

```
project/
|-- backend/
|   |-- main.py
|   |-- ollama_client.py
|   `-- utils.py
|
|-- frontend/
|   |-- index.html
|   |-- style.css
|   `-- script.js
|
|-- requirements.txt
`-- README.md
```

## Prerequisites

1. Python 3.10+
2. Ollama installed
3. A local model pulled and runnable (default: `llama3`)

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start Ollama model:

```bash
ollama run llama3
```

3. Start backend from `backend/` folder:

```bash
uvicorn main:app --reload
```

4. Open `frontend/index.html` in your browser.

## Backend API

### `POST /analyze`

Accepts multipart form data with either:

- `file` (uploaded source file)
- `code_text` (raw pasted code)

If both are provided, `code_text` is preferred.

Example response:

```json
{
  "explanation": "This code reads input and processes it...",
  "steps": [
    "Receive user input",
    "Parse the input",
    "Execute core logic",
    "Return output"
  ],
  "mermaid": "flowchart TD; A[Start] --> B[Process] --> C[End];"
}
```

## Configuration

Optional backend environment variables:

- `OLLAMA_URL` (default: `http://localhost:11434/api/generate`)
- `OLLAMA_MODEL` (default: `llama3`)
- `OLLAMA_TIMEOUT_SECONDS` (default: `90`)

## Error Handling

- Empty input validation (`400`)
- Large upload guard (`413`, max 500 KB)
- Ollama unavailable/timeouts (`502`)
- Unexpected server failures (`500`)

## Notes

- Mermaid is rendered client-side using CDN.
- CORS is enabled for local development.
- For best code-structure outputs, you can switch model to `codellama` by setting:

```bash
set OLLAMA_MODEL=codellama
```
