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

## Server Specifications (AWS)

| Component | Spec |
|-----------|------|
| **Instance** | g5.xlarge |
| **CPU** | AMD EPYC 7R32 — 4 vCPU (2 cores × 2 threads) |
| **RAM** | 16 GB |
| **GPU** | NVIDIA A10G — 24 GB VRAM |
| **OS** | Ubuntu (Debian-based) |
| **Inbound Port** | 8081/TCP (app UI/API) |

### Resource Allocation

| Resource | Container Limit | Container Reservation | Host Overhead |
|----------|----------------|----------------------|---------------|
| CPU | 3.5 vCPU | 2.0 vCPU | 0.5 vCPU |
| RAM | 14 GB | 8 GB | 2 GB |
| GPU | Full A10G (24 GB) | Full A10G | — |

## Deployment (AWS / Docker)

### 1. One-time server setup

```bash
# Install Docker
sudo apt-get update
sudo apt-get install -y docker.io docker-compose

# Install NVIDIA drivers (if not pre-installed)
sudo apt-get install -y nvidia-driver-535

# Install NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify GPU access
nvidia-smi
```

### 2. Clone and deploy

```bash
cd /opt/app
git clone https://github.com/Hariveerj/Code-Narator-AI.git
cd Code-Narator-AI

# Build and start (first run pulls model — takes a few minutes)
docker-compose build --no-cache
docker-compose up -d
```

### 3. Verify

```bash
# Check container status
docker-compose ps

# Check GPU inside container
docker exec codenarrator-app nvidia-smi

# Watch startup logs (model download on first boot)
docker logs -f codenarrator-app

# Health check
curl http://localhost:8081/health
curl http://localhost:8081/api/health/ollama
```

### 4. Update deployment

```bash
cd /opt/app/Code-Narator-AI
git pull
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### 5. AWS Security Group (Inbound Rules)

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 8081 | TCP | 0.0.0.0/0 (or your IP) | App UI & API |
| 22 | TCP | Your IP | SSH access |

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
