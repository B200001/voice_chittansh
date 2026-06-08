# 🎙️ Voice AI Platform — Multi-tenant SaaS

Production-ready **multi-tenant Voice AI SaaS** that enables businesses to deploy intelligent voice agents with minimal setup time. Built with FastAPI, LiveKit, and LLM-powered conversational flows.

New tenant onboarding reduced to **under 2 hours**.

## 🎯 Problem
Businesses want conversational voice AI but face high complexity in building multi-tenant, scalable voice infrastructure with proper authentication, telephony integration, and LLM orchestration.

## ✅ Solution
A complete **multi-tenant Voice AI platform** with:
- Workspace isolation
- JWT-based authentication
- Provider-agnostic telephony layer (currently integrated with Bolna)
- Real-time LLM-powered conversations using LiveKit

## ✨ Key Features

- **Multi-tenancy**: Complete workspace isolation with secure data separation
- **Fast Onboarding**: New clients can be onboarded in under 2 hours
- **Telephony Abstraction**: Swap voice providers without client-side changes
- **LLM Orchestration**: Structured prompt pipelines with fallback handling
- **Real-time Communication**: LiveKit for low-latency voice interactions
- **Webhook Support**: Robust event handling for call lifecycle

## 🛠️ Tech Stack

| Layer              | Technology                          |
|--------------------|-------------------------------------|
| **Backend**        | FastAPI, Python                     |
| **Frontend**       | Next.js, TypeScript                 |
| **Voice Engine**   | LiveKit                             |
| **Authentication** | JWT                                 |
| **Database**       | PostgreSQL                          |
| **Telephony**      | Bolna API (pluggable)               |
| **LLM**            | OpenAI / Anthropic / Self-hosted    |
| **Deployment**     | Docker, AWS                         |

## 📊 Results & Impact

- Successfully serving **3+ business clients** in production
- Reduced new tenant onboarding time to **under 2 hours**
- Built with clean separation of concerns and provider-agnostic design
- Production-grade architecture ready for scale

## 🏗️ Architecture Highlights

- Workspace-level data isolation
- JWT + webhook security model
- Pluggable telephony abstraction layer
- Real-time bidirectional audio streaming via LiveKit

## 🚀 Getting Started

```bash
git clone https://github.com/B200001/voice-ai-platform.git
cd voice-ai-platform

# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install && npm run dev
