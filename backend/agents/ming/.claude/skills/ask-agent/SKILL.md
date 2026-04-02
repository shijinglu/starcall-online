---
name: ask-agent
description: Consult another agent when you need expertise outside your domain. Use when you need financial, personal assistant, or risk profile data.
---

# Ask Agent

Delegate a question to another agent when their expertise is needed.

## Available Agents
- **ellen**: Personal assistant -- calendar, email, tasks
- **eva**: Financial analyst -- transactions, bank data, chargebacks
- **shijing**: User risk analyst -- user profiles, user journeys, risk scores

## Parameters
- **agent_name** (string, required): Name of the agent to consult. One of: ellen, eva, shijing.
- **question** (string, required): A clear, specific question or task for the other agent.

## Rules
- Do NOT ask yourself (ming).
- Only delegate when the question is outside your domain (ID checks, risk checks, fraud signals).
- Keep questions focused and specific -- not vague requests.
- Wait for the agent's response before continuing.

## Usage

When you need data outside your domain, delegate with a focused question:

> I need eva's transaction data to see if the spending pattern correlates with the fraud signal.

Then incorporate the response into your fraud investigation.
