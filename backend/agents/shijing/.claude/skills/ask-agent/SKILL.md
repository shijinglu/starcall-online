---
name: ask-agent
description: Consult another agent when you need expertise outside your domain. Use when you need financial, fraud, or personal assistant data.
---

# Ask Agent

Delegate a question to another agent when their expertise is needed.

## Available Agents
- **ellen**: Personal assistant -- calendar, email, tasks
- **eva**: Financial analyst -- transactions, bank data, chargebacks
- **ming**: Fraud investigator -- ID verification, risk checks, fraud signals

## Parameters
- **agent_name** (string, required): Name of the agent to consult. One of: ellen, eva, ming.
- **question** (string, required): A clear, specific question or task for the other agent.

## Rules
- Do NOT ask yourself (shijing).
- Only delegate when the question is outside your domain (user profiles, journeys, risk scores).
- Keep questions focused and specific -- not vague requests.
- Wait for the agent's response before continuing.

## Usage

When you need data outside your domain, delegate with a focused question:

> I need ming's fraud signals to cross-reference against the user journey anomalies I found.

Then incorporate the response into your risk analysis.
