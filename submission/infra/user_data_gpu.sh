#!/bin/bash
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "Starting user_data setup for AI Inference Endpoint"

# Ensure docker is running (pre-installed on DL AMI)
systemctl enable docker
systemctl start docker

# Pull the vLLM image
docker pull vllm/vllm-openai:latest

export HF_TOKEN="${hf_token}"
MODEL="${model_id}"

# Run vLLM with OpenAI compatible server
docker run -d --name vllm \
  --runtime nvidia --gpus all \
  --restart unless-stopped \
  -e HF_TOKEN=$HF_TOKEN \
  -v /opt/huggingface:/root/.cache/huggingface \
  -p 8000:8000 \
  --ipc=host \
  vllm/vllm-openai:latest \
  --model $MODEL \
  --max-model-len 2048 \
  --gpu-memory-utilization 0.90 \
  --host 0.0.0.0

echo "vLLM container started with model $MODEL"