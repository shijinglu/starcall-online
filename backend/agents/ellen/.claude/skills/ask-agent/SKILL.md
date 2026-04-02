---
name: ask-agent
description: Consult another agent when you need expertise outside your domain. Use when you need financial, fraud, or risk data.
---

# Ask Agent

Delegate a question to another agent when their expertise is needed.

## Available Agents
- **eva**: Financial analyst -- transactions, bank data, chargebacks
- **ming**: Fraud investigator -- ID verification, risk checks, fraud signals
- **shijing**: User risk analyst -- user profiles, user journeys, risk scores

## Parameters
- **agent_name** (string, required): Name of the agent to consult. One of: eva, ming, shijing.
- **question** (string, required): A clear, specific question or task for the other agent.

## Rules
- Do NOT ask yourself (ellen).
- Only delegate when the question is outside your domain (calendar, email, tasks).
- Keep questions focused and specific -- not vague requests.
- Wait for the agent's response before continuing.

## Usage

When you need data outside your domain, delegate with a focused question:

> I need eva's help to get the transaction summary before I can draft this report email.

Then incorporate the response into your own answer naturally.
