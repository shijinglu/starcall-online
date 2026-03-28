
This is an AI conversation & digital agent system consists of:

1. A client app, which has user interface that allows user to communicate with backend AI agents via voice. The UI functions like gemini app live mode, chatgpt void mode and claude code voice mode.

2. A backend that can transcript the voice into text, process the text as AI commands, convert AI responded text into voice and return both text and voice responses to the client.

## Use cases and flows

### Example Case 1: Natural conversation flow with fast response

* **User flow**

User opens the app on their phone, and the app has a button to start a conversation. Once started, the app always listen, user can talk at anytime and can also interrupt the response. The APP listens, start responding if user completed talking, or it detects a pause.

The flow is like:

<<user pushed start button>>
(user): "tell me something new about gemini" <<pause>>
(app): "good morning, let me search news about the crypto exchange gemini ..."
(user): "no, no, no, it is not the crypto exchange gemini, I am talking about Google AI product Gemini."
(app): "cover that, let me search news about Google Gemini..."


* **The Architecture**: How "Barge-in" Works
For a natural flow, the system must handle two things simultaneously:

1. VAD (Voice Activity Detection): The API automatically detects when you start and stop speaking.
2. Interruption Logic: When you start talking while the AI is mid-sentence, the server sends an interruption event. Your frontend must immediately stop the local audio playback to "clear the floor."


### Example Case 2: Nature conversation with both fast and slow responses.

Similar to the previous one, one can push the button to start a conversation, but this time, in the backend, the user configured an AI sub-agent who responds a bit slower but think deeper. 

The conversation will be something like:
<<user pushed start button>>
(user): "help me pull the TODO items from work for today" <<pause>>
(app): "okie, I have notified your digital assistant Ellen, she will update your shortly!"
(user): "also, help me review yesterday business metrics and brief me with a summary."
(app): "cover that, just notified Ellen on that as well..."
(ellen): "hi boss, following is your work for today: you have two meetings in the morning ..."
(user): "skip the morning schedules, help me check if there are arrangements at dinner time"
(ellen): "copy on that, I just checked, you have 1 meeting from 5PM to 5:30PM, do you want me to cancel that?"
(user): "yes, cancel that for me please"
(ellen): "done, do you want to continue with schedule or review the metrics"
(user): "review the metrics"
(ellen): "great, following is a summary of yesterday's metrics: ..."


### Example Case 3: Call a meeting

In the previous flow, there are one fast responder, a deep thinking digital assitant. This flow adds another layer of complexity to the previous:

In this mode, user can conjure a meeting with many deep thinking digital agent. The fast responder will serve as the moderator

The conversation will be something like:
<<user pushed start button>>
(user): "help me call shijing, eva and ming" <<pause>>
(app): "okie, I have shijing, eval and ming is online, what's up boss!"
(user): "From yesterday's metrics, I saw a spike in ACH return case, what the frac is going on?"
<<in the backend, gemini-live calls deep agents asynchronously>>
(app): "okie, boss, looks like shijing eva and ming are working on it. Do you want to hear a joke"
(ellen): "hi boss, following is your work for today: you have two meetings in the morning ..."
(user): "no! just be quick"
(eva): "ok, boss. Looks like all the returns are from one user, who is mid age with good finance condition based on the bank statements, good credit score, not sure what is going on. Continue digging"
(shijing): "from user profile and user journey perspective, the user has passed all identity checks, all risk checks passed without service anomally. One minior thing is that the user tried KYC 3 times"
(ming): "based on some quick data scan, looks like the user barely passed ID check, and based on the async risk check, there is a good chance the user stole ID of someone else".
(user): "ok, please continue research, each of you write a summary of your findings, I would like to see your investigations in my mailbox in 10 minutes"
(eva): "copy on that"
(shijing): "will do"
(ming): "working on it"


### Example Case 4: Quick lookup (moderator-only, no deep agent)

User asks something the fast moderator can handle directly with a built-in tool call (search, time lookup, calculator). No deep agent is dispatched.

The conversation will be something like:
<<user pushed start button>>
(user): "what time is it in london right now?" <<pause>>
(app): "it is 3:15 PM in London."
(user): "and what about tokyo?"
(app): "7:15 AM tomorrow in Tokyo."
(user): "thanks, set a reminder for me to join a call at 9 AM tokyo time"
(app): "done, reminder set for 8 AM your local time."


### Example Case 5: Delegated task, user moves on, agent reports back proactively

User fires off a task and immediately changes topic. The deep agent runs in the background and injects its result into the conversation when ready — without the user having to ask.

The conversation will be something like:
<<user pushed start button>>
(user): "Ellen, please draft a summary of last week's fraud incidents and send it to the team." <<pause>>
(app): "got it, Ellen is on it."
(user): "also, what is the current ACH processing volume?"
(app): "processing volume is $4.2M in the last hour."
(ellen): "hi boss, draft is ready and sent to the team. Subject: Fraud Summary Week 12. Three key incidents were flagged..."
(user): "good. anything urgent in there?"
(ellen): "one item — the synthetic ID cluster in the EU region needs a rule update."


### Example Case 6: Multi-turn follow-up with the same deep agent

User delegates a task, then asks follow-up questions that are routed back to the same running agent session rather than spawning a new one.

The conversation will be something like:
<<user pushed start button>>
(user): "Ellen, analyze the chargeback spike from last night." <<pause>>
(app): "on it, Ellen is digging in."
(ellen): "boss, the spike is concentrated in three merchants, all in the electronics category."
(user): "which merchant had the highest volume?"
(ellen): "MerchantX had 47 chargebacks, roughly 60% of the total."
(user): "pull the top 5 user IDs behind those chargebacks."
(ellen): "here are the top 5: user IDs 1023, 4857, 9901, 3312, 0088."


## Tech Stack

| Component | Technology | Comment |
| :--- | :--- | :--- |
| **Frontend** | Native iOS (Swift) | `AVAudioEngine` for realtime PCM streaming; `AVAudioSession` for routing/interruptions |
| **Backend** | Python fastapi, gemini live api and claude code python sdk | gemini is like a moderator |
| **Voice loop** | Gemini Multimodal Live API | Handles VAD, barge-in, TTS with voice copy |
| **Fast moderator** | **Gemini Flash** | Built into the Realtime session |

Other choices:
- Persistence: sqlite
- Live API: directly use `google-genai`, avoid using complicated LLM frameworks
- Deep agent: use `claude-agent-sdk-python`

---

## Component breakdown

**Client app** — a native iOS app (Swift). `AVAudioEngine` taps the microphone at the hardware level and streams raw PCM chunks to the backend via WebSocket. Incoming TTS audio chunks are queued and played back through `AVAudioPlayerNode`. On receiving an interruption event, the app immediately flushes the playback queue and stops the player node — that's the entire barge-in implementation. `AVAudioSession` handles routing changes (AirPods, speaker, earpiece) and system interruptions (phone calls, Siri) automatically.

**Realtime voice layer** — The python fastapi backend proxies the client's audio stream straight to the Gemini Live API, and relays the TTS audio response back. 

**Fast moderator** — this runs inside the Gemini Live session as the "system prompt persona." It handles cases 1 and 2's quick acknowledgments (`"okie, notifying Ellen now!"`), decides whether a request needs a deep agent, and dispatches via **function calling**. The function call is the signal to your backend to spin up a slow agent.

**Deep agents (Ellen, domain experts)** — each is a standalone Claude SDK call with a custom system prompt defining the persona, a tool set, and access to relevant APIs. They run asynchronously. When they complete, you inject their text response back into the Realtime API session as an "assistant turn" and it synthesizes the voice automatically.

**Meeting mode (case 3)** — same mechanism, just N agents dispatched in parallel. The fast moderator acts as emcee — it acknowledges the user immediately while agents work, then voices their results as they arrive. Each agent is just another async Claude call tagged with a name.

