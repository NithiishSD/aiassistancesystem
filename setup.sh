#!/bin/bash
set -e

echo "=== Jarvis Phase 1: Environment Setup ==="

# 1. Install Ollama (local model runner)
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Ollama already installed."
fi

# 2. Start Ollama service in background (if not running)
if ! pgrep -x "ollama" > /dev/null; then
    echo "Starting Ollama service..."
    ollama serve &
    sleep 3
fi

# 3. Pull the local models
echo "Pulling Qwen2.5-Coder-7B (coding agent)..."
ollama pull qwen2.5-coder:7b

echo "Pulling Llama3.1-8B (routing / general reasoning)..."
ollama pull llama3.1:8b

# 4. Create Conda environment
echo "Using Conda environment 'zedek-env' (Python 3.12)..."
if ! command -v conda &> /dev/null; then
    echo "[ERROR] conda not found in PATH. Activate your conda installation first (e.g. 'source ~/miniconda3/etc/profile.d/conda.sh') and re-run."
    exit 1
fi

# Needed so 'conda activate' works inside a non-interactive script shell
source "$(conda info --base)/etc/profile.d/conda.sh"

if ! conda env list | grep -q "${PWD}/zedek-env"; then
    conda create --prefix zedek-env python=3.12
fi
conda activate zedek-env

# 5. Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 6. Cache the local semantic-router embedding model before offline startup.
# The classifier sets HF_HUB_OFFLINE=1 during normal operation, so this
# explicit one-time download must happen while network access is available.
echo "Caching local intent and memory embedding model..."
mkdir -p models/all-MiniLM-L6-v2
HF_HUB_OFFLINE=0 python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='sentence-transformers/all-MiniLM-L6-v2', local_dir='models/all-MiniLM-L6-v2')"

echo ""
echo "=== Setup complete ==="
echo "Activate the environment with: conda activate ./zedek-env"
echo "Then run: python test_setup.py"
