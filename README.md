# 🤖 Agentic RAG Code Reviewer

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?style=for-the-badge&logo=fastapi)
![Celery](https://img.shields.io/badge/Celery-5.3%2B-37814A?style=for-the-badge&logo=celery)
![Redis](https://img.shields.io/badge/Redis-7.0-DC382D?style=for-the-badge&logo=redis)
![Qdrant](https://img.shields.io/badge/Qdrant-Vector%20DB-FF5252?style=for-the-badge&logo=qdrant)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker)

An **Enterprise-Grade, Agentic RAG-powered Code Review System**. This project leverages Large Language Models (LLMs) and Vector Databases to automatically review GitHub Pull Requests, provide context-aware suggestions, and maintain a self-evolving knowledge base of corporate coding guidelines.

## ✨ Core Features & Architecture Highlights

- ⚡ **Zero-Drop Asynchronous Queue**: Built on **Celery + Redis**, completely decoupling the GitHub Webhook (FastAPI) from heavy LLM inference. Eliminates GitHub's 10-second timeout constraints and ensures tasks survive container restarts (Robust Chaos Engineering).
- 🐳 **One-Click Containerization**: Fully orchestrated via `docker-compose`. Includes Web API, Celery Worker, Redis Message Broker, and Qdrant DB.
- 🔒 **Privacy & Offline Support**: Uses local HuggingFace Embedding models (`BAAI/bge-small-zh-v1.5`) with physical cache mounting (`HF_HUB_OFFLINE=1`). Embeddings are calculated strictly on-premise without network tracking.

---

## 💡 Advanced Usage & Agentic Workflows

This system is not a simple "prompt-in, prompt-out" wrapper. It implements a multi-agent orchestrated workflow using LangGraph concepts.

### 1. 🚦 Multi-Tier Routing (Tier-X)

To prevent wasting high-cost LLM tokens on trivial changes, the system supports dynamic routing based on PR metadata. Developers can control the depth of the review by adding a metadata block to their PR description:

```text
>>>REVIEW_METADATA<<<
Tier: Tier-S
Context: Implement core payment transaction lock.
Focus: Race conditions, deadlock prevention, ACID compliance.
>>>END<<<
```

- **Tier-S (Deep Architecture)**: Triggers deep file-tree scanning and strict architectural compliance checks.
- **Tier-A (Standard)**: Thorough logical review.
- **Tier-B (Fast-Path)**: Lightweight syntax, style, and obvious bug checks (Default).

### 2. 💬 Interactive Refiner & Human-in-the-Loop (`@bot`)

If the AI suggests a modification but you want a different approach, or if you need the AI to elaborate, simply reply to the PR comment:
> `@bot please rewrite this using a switch-case statement instead.`

The **Chat Pipeline** will instantly trigger, read the conversation history, and generate a newly revised code snippet in the thread.

### 3. 🛡️ False Positive Handling & Agentic Self-Correction

Code review bots are notorious for generating noisy, false-positive comments. This system implements an **Agentic Critic mechanism**:

1. **Draft Generation**: The primary Reviewer Agent drafts comments based on the code diff.
2. **Critic Evaluation**: A secondary Critic Agent evaluates the draft against corporate guidelines retrieved from Qdrant.
3. **Self-Correction**: If the Critic flags a comment as trivial, hallucinatory, or a "false positive" (e.g., complaining about a missing import that exists in another file), the comment is automatically dropped before it ever reaches GitHub.

### 4. 🧠 Dynamic Knowledge Injection (Advanced RAG 2.0)

Our RAG implementation goes far beyond naive chunk-and-search vector retrieval. It implements a **Hybrid Retrieval** architecture to bridge the semantic gap between code diffs and natural language:

- **Query Rewriting (HyDE Variant)**: Direct vector matching between code symbols (`+ import os`) and natural language rules often fails due to the semantic gap. We use an LLM to dynamically translate code diffs into "intent descriptions" before querying Qdrant, drastically improving recall accuracy.
- **Structured Metadata Schema**: Every guideline injected into Qdrant is tagged with a strict schema (`category`, `language`, `path_regex`, `severity`). The retrieval process uses Qdrant's `QueryFilter` to perform hard-filtering (e.g., Python rules will never pollute Go file reviews).
- **Two-Phase Refiner Pipeline**: When a developer rejects an AI comment via the GitHub thread (`@bot this is a false positive`), the system triggers a two-phase pipeline:
  1. **Intent Classification**: Determines if the comment is a true "False Positive Rejection" or just "General Chat", preventing conversational garbage from polluting the vector DB.
  2. **Conditional Extraction**: If classified as a false positive, it extracts the domain context and injects a `staging_patch` into Qdrant. Future PRs will pull this patch, effectively granting the system a self-healing memory.
---

## 🏗️ System Architecture

The system consists of 4 isolated Docker containers communicating via a dedicated Docker network:

1. **Gateway Node (FastAPI)**: Lightweight webhook listener. Validates GitHub HMAC signatures, parses PR payloads, and enqueues tasks.
2. **Message Broker (Redis)**: High-performance memory buffer. Absorbs traffic spikes and guarantees task delivery.
3. **Compute Node (Celery Worker)**: The heavy lifter. Downloads diffs, queries the vector database, prompts the LLM, and posts review comments back to GitHub.
4. **Memory Node (Qdrant)**: Persistent vector storage for corporate knowledge and developer guidelines.

```mermaid
graph TD;
    GitHub[GitHub Webhook] -->|Push/PR Event| API[FastAPI Gateway]
    API -->|Enqueue Task| Redis[(Redis Broker)]
    API -.->|Return 200 OK| GitHub
    Redis -->|Consume Task| Worker[Celery Worker]
    Worker <-->|RAG Query/Upsert| Qdrant[(Qdrant Vector DB)]
    Worker <-->|Embedding| LocalHF[Local HuggingFace Model]
    Worker <-->|Inference & Agentic Critic| LLM[DeepSeek/OpenAI LLM]
    Worker -->|Post Review| GitHub
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Docker & Docker Compose
- A GitHub repository with Webhooks enabled
- DeepSeek / OpenAI API Key

### 2. Environment Setup

Create a `.env` file in the root directory:

```env
# GitHub Auth
GITHUB_TOKEN=ghp_your_classic_repo_token_here
GITHUB_WEBHOOK_SECRET=your_secret_string

# LLM Configuration
DEEPSEEK_API_KEY=sk-your_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Component URLs (Internal Docker Network)
QDRANT_URL=http://qdrant:6333
REDIS_URL=redis://redis:6379/0
```

### 3. Deploy Infrastructure

Run the following command to build the images and start the distributed system:

```bash
docker-compose up -d --build
```

### 4. Verify Services

You can verify the robust architecture by inspecting the container logs:

```bash
# Watch the Gateway receive instant webhooks
docker logs code_reviewer_api -f

# Watch the Worker execute AI inference asynchronously
docker logs code_reviewer_worker -f
```

---

## 🔧 Technical Implementations (For Interviewers)

- **Proxy & TLS MITM Resilience**: Configured `httpx.AsyncClient(verify=False)` and `HF_HUB_OFFLINE=1` to ensure the system survives enterprise VPN environments and TLS interception without crashing.
- **Cache Persistence**: Deep learning model weights (`bge-small-zh`) are mounted natively from the host to the container via `volumes`, reducing initialization time from minutes to milliseconds.
- **Graceful Error Handling**: Implemented multi-layered exception mapping for `httpx.ConnectError` and GitHub API rate-limiting, ensuring the background worker gracefully retries failed submissions.

---
*Built with ❤️ for next-generation developer productivity.*
