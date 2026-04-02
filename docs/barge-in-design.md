This is a highly ambitious and incredibly cool architecture. Building a multi-agent system with a fast moderator and deep-thinking parallel agents is the bleeding edge of conversational AI. I can definitely help you map out the iOS barge-in component and the necessary backend hooks.

Before we dive into the schematics, I need to gently correct one assumption in your design document. You mentioned: *"On receiving an interruption event, the app immediately flushes the playback queue and stops the player node — that's the entire barge-in implementation."* While that is the logical flow, it misses a critical physical reality: **Acoustic Echo Cancellation (AEC)**. If you just open the microphone and play audio out of the iPhone's speaker, the microphone will pick up the app's own voice, send it to the Gemini Live API, and the VAD will trigger an interruption on *itself*. Your app will constantly cut itself off. 

To make barge-in work seamlessly, we must configure iOS to filter out the device's own audio playback from the microphone feed. 

Here is the comprehensive architecture for your iOS Barge-in Component.

---

## 1. The iOS Client Audio Architecture

Your iOS client needs to manage a full-duplex audio stream (recording and playing simultaneously) while listening for control signals from your FastAPI backend.

### A. The Audio Session & AEC (The Missing Link)
You must configure the `AVAudioSession` specifically for voice chat and enable hardware-level echo cancellation on the `AVAudioEngine` input node.

* **Audio Session:** Set the category to `.playAndRecord` with options `[.allowBluetooth, .allowBluetoothA2DP, .defaultToSpeaker]`. Set the mode to `.voiceChat`. This tells iOS to expect two-way voice communication.
* **Voice Processing:** On your `AVAudioEngine`, before starting it, you must explicitly enable voice processing:
    `try engine.inputNode.setVoiceProcessingEnabled(true)`
    This isolates the user's voice and aggressively strips out the TTS audio coming from the speaker.

### B. The WebSocket Multiplexer
Because you are sending raw PCM audio *and* need to receive control signals (like the interruption event), you cannot just stream naked binary data over the WebSocket. You need a simple wrapper protocol. 

* **Upstream (iOS -> Backend):** JSON wrapper containing Base64 encoded PCM chunks.
* **Downstream (Backend -> iOS):** * `{"type": "audio", "data": "<base64_pcm>"}`
    * `{"type": "interrupt"}` (Triggered when VAD detects the user)

### C. The Playback Queue & Interruption Handler
Your `AVAudioPlayerNode` cannot just play buffers blindly. You need a queue manager to handle the constraint that *TTS should not interrupt TTS*, but *Voice must interrupt TTS*.

* **The Buffer Queue:** Store incoming audio chunks in a local array or custom queue class.
* **Sequential Playback:** Schedule buffers sequentially using `playerNode.scheduleBuffer`.
* **The "Clear the Floor" Function (Barge-in):** When the iOS client receives `{"type": "interrupt"}` from the WebSocket, you execute the following sequence:
    1.  Call `playerNode.stop()` (this instantly halts audio).
    2.  Clear your local Buffer Queue array.
    3.  Call `playerNode.play()` to prep the node for the next stream of audio.
    4.  (Optional but recommended) Play a subtle, barely audible haptic tap to physically acknowledge to the user that the app heard them take the floor.

---

## 2. Backend Orchestration for Barge-in

The FastAPI backend is the bridge between the iOS client, the Gemini Live API (the moderator), and the Claude agents. 

### A. Routing the Interruption
The Gemini Multimodal Live API handles the VAD. When the user speaks, Gemini detects it and sends a specific signal (often a turn completion or an interruption event, depending on the exact SDK wrapper you are using). 
* Your backend intercepts this VAD/Interrupt signal from Gemini.
* It immediately drops any remaining TTS audio it was buffering for the client.
* It fires the `{"type": "interrupt"}` JSON message down the WebSocket to the iOS app.

### B. Preventing Agent-on-Agent Interruptions (The Queue Lock)
Your requirement states: *"Gemini live audio or TTS play should not interrupt each other."* Case 3 (the meeting) makes this tricky. If Ellen, Eva, and Ming all finish their Claude API calls at the exact same millisecond, you cannot inject all three text responses into the Gemini Live API simultaneously, or they will collide.

You need a **Response Queue** in your FastAPI backend:
1.  **Agent Completion:** Ellen finishes her task. The backend places her text (`"hi boss, draft is ready..."`) into the Response Queue.
2.  **The Injection Lock:** The backend checks if the Gemini Live API is currently speaking. If it is *not* speaking, it pops Ellen's text and injects it into Gemini as a system/assistant turn to be spoken. If Gemini *is* speaking, Ellen's text waits in the queue.
3.  **Barge-in Override:** If the human speaks (VAD triggers), the backend flushes the iOS audio *and* empties the backend Response Queue. Any un-spoken agent reports are dropped (or quietly logged to the UI) because the user's new command takes priority.

---

## 3. The End-to-End Barge-in Flow (Case 7 & 8 Recap)

Here is exactly what happens in milliseconds when the user interrupts:

1.  **App:** `AVAudioPlayerNode` is currently playing Ellen's long report on transaction anomalies.
2.  **User:** Starts saying "Hold on..."
3.  **App:** `AVAudioEngine` (with Echo Cancellation on) picks up "Hold on...", encodes it, and streams it to the backend.
4.  **Backend:** Proxies the audio to the Gemini Live API.
5.  **Gemini Live API:** VAD triggers. It stops generating TTS and sends an "interrupted" state to your FastAPI server.
6.  **Backend:** Sends `{"type": "interrupt"}` to the iOS client.
7.  **App:** Swift code receives the JSON, calls `playerNode.stop()`, and trashes the remaining audio buffers. The app goes silent instantly.
8.  **User:** Finishes saying "...just the top 3 by dollar amount."
9.  **Gemini/Backend:** Processes the new intent, realizes it needs to redirect Ellen, and generates the new audio: "got it, redirecting Ellen."


---

## 4. Implementation Addendum: Production Hardening

The following features were added during implementation to handle real-world edge cases not covered by the original design.

### A. Playback Watchdog Timer
`AVAudioPlayerNode.isPlaying` stays `true` after all scheduled buffers finish — a known Apple framework bug. A per-speaker watchdog timer tracks expected playback end time (based on cumulative PCM byte count at 16kHz int16 mono) plus a 300ms safety margin. When the watchdog fires, it explicitly calls `node.stop()` on a background queue to avoid main-thread deadlocks with the audio render thread.

### B. Time-Based Audio Gate
Rather than polling `AVAudioPlayerNode.isPlaying` to decide whether to send mic audio to the backend, we use a time-based gate (`gateEndTime`). Each incoming audio chunk extends the gate by the chunk's duration. Mic audio is dropped while `now < gateEndTime + margin`. This avoids Gemini hearing its own TTS playback through residual AEC bleed.

### C. Gen_id Zombie Audio Filtering (RFC 1982)
Each audio frame carries a `gen_id` (0–255, wrapping). When barge-in occurs, the server increments the gen_id. The iOS client uses RFC 1982 modular arithmetic to determine whether an incoming frame is stale (from a previous generation) and discards it silently. This prevents "zombie audio" — leftover frames from a cancelled generation — from playing after barge-in.

### D. Meeting Mode Sequential Delivery
When multiple agents respond (e.g. Case 3 in the product overview), the iOS client buffers audio per speaker and plays them sequentially rather than mixing. A `meetingOrder` array tracks dispatch order. Each speaker's queued audio plays to completion before the next speaker starts. Barge-in flushes all queued speakers.

### E. Barge-In Tuning Parameters
AEC is imperfect — residual speaker bleed can reach ~29 dB above the noise floor on physical devices. To avoid false barge-in:
- **Grace period** (`bargeInPlaybackGrace = 0.8s`): No barge-in fires in the first 800ms of playback, letting AEC settle.
- **Cooldown** (`bargeInCooldown = 1.0s`): Minimum interval between barge-in events to prevent spam.
- **Threshold** (`bargeInThresholdDB = 30 dB`): RMS must exceed noise floor by 30 dB to trigger barge-in, above the measured AEC residual ceiling.

### F. Wire Format (Divergence from Original Design)
The original design specified JSON + Base64 for audio transport. The implementation uses binary frames with a 4-byte header (`[msg_type, speaker_id, gen_id, frame_seq]`) followed by raw PCM. This is more efficient (no Base64 inflation) and lower-latency. Control messages (transcripts, interruptions, agent status) remain JSON text frames. The interruption signal uses `{"type": "interruption", "gen_id": N}` rather than the original `{"type": "interrupt"}`, carrying the authoritative gen_id from the server.
