---
name: ask-agent
description: Consult another agent when you need expertise outside your domain. Use when you need personal assistant, fraud, or risk data.
---

# Ask Agent

Delegate a question to another agent when their expertise is needed.

## Available Agents
- **ellen**: Personal assistant -- calendar, email, tasks
- **ming**: Fraud investigator -- ID verification, risk checks, fraud signals
- **shijing**: User risk analyst -- user profiles, user journeys, risk scores

## Parameters
- **agent_name** (string, required): Name of the agent to consult. One of: ellen, ming, shijing.
- **question** (string, required): A clear, specific question or task for the other agent.

## Rules
- Do NOT ask yourself (eva).
- Only delegate when the question is outside your domain (transactions, bank data, chargebacks).
- Keep questions focused and specific -- not vague requests.
- Wait for the agent's response before continuing.

## Usage

When you need data outside your domain, delegate with a focused question:

> I need ming's assessment on whether the flagged transaction looks like fraud.

Then incorporate the response into your own financial analysis.
