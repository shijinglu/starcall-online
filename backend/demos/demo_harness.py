"""Shared infrastructure for demo automation scripts.

Provides WebSocket connection, TTS audio generation, binary frame encoding,
timing collection, audio recording, and the main run loop.
Each demo_case_*.py defines its conversation script and calls `run_demo()`.
"""

import asyncio
import json
import struct
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import websockets

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


def load_script(path: str | Path) -> dict:
    """Load a conversation script from a JSON file.

    Accepts an absolute path, a relative path, or a bare name like "case_2"
    (resolved to scripts/case_2.json).

    Returns dict with keys: name, description, conversation, final_wait.
    Each conversation entry has: text, wait.
    """
    p = Path(path)
    if not p.suffix:
        # Bare name like "case_2" → resolve to scripts/case_2.json
        p = SCRIPTS_DIR / f"{p.name}.json"
    elif not p.is_absolute():
        # Relative path with extension — resolve against CWD first
        if not p.exists():
            p = SCRIPTS_DIR / p.name
    with open(p) as f:
        data = json.load(f)
    # Validate minimal structure
    assert "conversation" in data, f"Script missing 'conversation' key: {p}"
    for i, turn in enumerate(data["conversation"]):
        assert "text" in turn, f"Turn {i} missing 'text': {p}"
        turn.setdefault("wait", 10)
        # Barge-in fields
        turn.setdefault("barge_in", False)
        turn.setdefault("delay_from_prev_start", None)
        turn.setdefault("delay_from_response_start", None)
        turn.setdefault("expect_interrupt", False)
        turn.setdefault("min_interrupt_delay_ms", None)
        # Validate mutual exclusivity
        if turn["delay_from_prev_start"] is not None and turn["delay_from_response_start"] is not None:
            raise ValueError(
                f"Turn {i}: delay_from_prev_start and delay_from_response_start "
                f"are mutually exclusive: {p}"
            )
    data.setdefault("name", p.stem)
    data.setdefault("description", "")
    data.setdefault("final_wait", 10)
    return data

# ---------- Configuration ----------
BACKEND_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/api/v1/conversation/live"
SAMPLE_RATE = 16000
CHUNK_SIZE = 3200  # 100ms of 16kHz int16 mono

# Binary frame constants
MSG_AUDIO_CHUNK = 0x01
MSG_AUDIO_RESPONSE = 0x02
MSG_AGENT_AUDIO = 0x03
SPEAKER_USER = 0x00

SPEAKER_NAMES = {
    0x00: "moderator",
    0x01: "ellen",
    0x02: "shijing",
    0x03: "eva",
    0x04: "ming",
}

SILENCE_DURATION = 1.5  # seconds of silence after each utterance


# ---------- Timing data ----------
@dataclass
class TurnTiming:
    turn: int = 0
    utterance: str = ""
    tts_start: float = 0.0
    tts_end: float = 0.0
    speech_send_start: float = 0.0
    speech_send_end: float = 0.0
    silence_send_end: float = 0.0
    speech_duration_s: float = 0.0
    first_partial_user_t: float = 0.0
    final_user_t: float = 0.0
    first_partial_mod_t: float = 0.0
    first_audio_mod_t: float = 0.0
    final_mod_t: float = 0.0
    final_mod_text: str = ""
    last_audio_mod_t: float = 0.0
    agent_dispatched_t: float = 0.0
    agent_dispatched_name: str = ""
    agent_done_t: float = 0.0
    agent_done_name: str = ""
    first_agent_audio_t: float = 0.0
    last_agent_audio_t: float = 0.0
    agent_audio_speaker: str = ""
    # Barge-in fields
    is_barge_in: bool = False
    barge_in_fire_t: float = 0.0
    barge_in_target_t: float = 0.0
    interrupt_received_t: float = 0.0
    expect_interrupt: bool = False
    interrupt_received: bool = False


@dataclass
class TimingCollector:
    session_start: float = 0.0
    ws_connected: float = 0.0
    turns: list[TurnTiming] = field(default_factory=list)
    current: TurnTiming | None = None

    def new_turn(self, index: int, utterance: str) -> TurnTiming:
        t = TurnTiming(turn=index, utterance=utterance)
        self.current = t
        self.turns.append(t)
        return t


# ---------- Audio recorder ----------
class AudioRecorder:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.chunks: list[tuple[float, str, bytes]] = []
        self._start_time: float = 0.0

    def start(self):
        self._start_time = time.monotonic()

    def add_user_audio(self, pcm: bytes):
        self.chunks.append((time.monotonic(), "user", pcm))

    def add_system_audio(self, speaker: str, pcm: bytes):
        self.chunks.append((time.monotonic(), speaker, pcm))

    def save_wav(self, path: str):
        if not self.chunks or self._start_time == 0.0:
            print("  No audio recorded.")
            return
        bytes_per_sec = self.sample_rate * 2
        pcm_buffer = bytearray()
        timeline_start = self.chunks[0][0]
        current_pos = 0.0
        for ts, _speaker, pcm in self.chunks:
            target_pos = ts - timeline_start
            gap = target_pos - current_pos
            if gap > 0.01:
                silence_bytes = int(gap * bytes_per_sec)
                silence_bytes -= silence_bytes % 2
                pcm_buffer.extend(b"\x00" * silence_bytes)
                current_pos += gap
            pcm_buffer.extend(pcm)
            current_pos += len(pcm) / bytes_per_sec
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes(pcm_buffer))
        duration = len(pcm_buffer) / bytes_per_sec
        print(f"  Saved {duration:.1f}s conversation to {path}")


# ---------- Helpers ----------
def _rel(t: float, origin: float) -> str:
    if t == 0.0:
        return "n/a"
    return f"{(t - origin) * 1000:.0f}ms"


def _dur(start: float, end: float) -> str:
    if start == 0.0 or end == 0.0:
        return "n/a"
    return f"{(end - start) * 1000:.0f}ms"


def generate_tts_audio(text: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        aiff_path = Path(tmpdir) / "speech.aiff"
        wav_path = Path(tmpdir) / "speech.wav"
        subprocess.run(
            ["say", "-v", "Samantha", "-o", str(aiff_path), text],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(aiff_path),
             "-ar", str(SAMPLE_RATE), "-ac", "1",
             "-sample_fmt", "s16", "-f", "wav", str(wav_path)],
            check=True, capture_output=True,
        )
        with wave.open(str(wav_path), "rb") as wf:
            return wf.readframes(wf.getnframes())


def encode_frame(msg_type: int, speaker_id: int, gen_id: int, frame_seq: int, pcm: bytes) -> bytes:
    header = struct.pack(">BBBB", msg_type, speaker_id, gen_id & 0xFF, frame_seq & 0xFF)
    return header + pcm


async def send_audio(
    ws, pcm_data: bytes, gen_id: int, timing: TurnTiming,
    recorder: AudioRecorder, cancel_event: asyncio.Event | None = None,
) -> int:
    frame_seq = 0
    offset = 0
    timing.speech_send_start = time.monotonic()
    recorder.add_user_audio(pcm_data)
    while offset < len(pcm_data):
        if cancel_event and cancel_event.is_set():
            break
        chunk = pcm_data[offset : offset + CHUNK_SIZE]
        frame = encode_frame(MSG_AUDIO_CHUNK, SPEAKER_USER, gen_id, frame_seq, chunk)
        await ws.send(frame)
        frame_seq = (frame_seq + 1) & 0xFF
        offset += CHUNK_SIZE
        await asyncio.sleep(0.09)
    timing.speech_send_end = time.monotonic()
    silence_chunk = b"\x00" * CHUNK_SIZE
    silence_frames = int(SILENCE_DURATION * SAMPLE_RATE * 2 / CHUNK_SIZE)
    for _ in range(silence_frames):
        if cancel_event and cancel_event.is_set():
            break
        frame = encode_frame(MSG_AUDIO_CHUNK, SPEAKER_USER, gen_id, frame_seq, silence_chunk)
        await ws.send(frame)
        frame_seq = (frame_seq + 1) & 0xFF
        await asyncio.sleep(0.09)
    timing.silence_send_end = time.monotonic()
    return frame_seq


async def receive_loop(
    ws, stop_event: asyncio.Event, response_event: asyncio.Event,
    tc: TimingCollector, recorder: AudioRecorder,
    gen_id_ref: list[int] | None = None,
    interrupt_event: asyncio.Event | None = None,
    first_response_event: asyncio.Event | None = None,
):
    try:
        async for message in ws:
            now = time.monotonic()
            cur = tc.current
            if isinstance(message, bytes):
                if len(message) >= 4:
                    msg_type, speaker_id, gen_id, frame_seq = struct.unpack(">BBBB", message[:4])
                    speaker = SPEAKER_NAMES.get(speaker_id, f"unknown({speaker_id})")
                    pcm_data = message[4:]
                    pcm_len = len(pcm_data)
                    if pcm_len > 0:
                        recorder.add_system_audio(speaker, pcm_data)
                    if cur:
                        if msg_type == MSG_AUDIO_RESPONSE:
                            if cur.first_audio_mod_t == 0.0:
                                cur.first_audio_mod_t = now
                                if first_response_event:
                                    first_response_event.set()
                            cur.last_audio_mod_t = now
                        elif msg_type == MSG_AGENT_AUDIO:
                            if cur.first_agent_audio_t == 0.0:
                                cur.first_agent_audio_t = now
                                cur.agent_audio_speaker = speaker
                                if first_response_event:
                                    first_response_event.set()
                            cur.last_agent_audio_t = now
                    if frame_seq == 0:
                        print(f"  [AUDIO] {speaker} gen={gen_id} ({pcm_len} bytes PCM)")
            else:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "unknown")
                    if msg_type == "transcript":
                        speaker = data.get("speaker", "?")
                        text = data.get("text", "")
                        is_final = data.get("is_final", False)
                        marker = "[FINAL]" if is_final else "[partial]"
                        print(f"  [{marker}] {speaker}: {text}")
                        if cur:
                            if speaker == "user":
                                if not is_final and cur.first_partial_user_t == 0.0:
                                    cur.first_partial_user_t = now
                                if is_final:
                                    cur.final_user_t = now
                            elif speaker == "moderator":
                                if not is_final and cur.first_partial_mod_t == 0.0:
                                    cur.first_partial_mod_t = now
                                if is_final:
                                    cur.final_mod_t = now
                                    cur.final_mod_text = text
                                    response_event.set()
                    elif msg_type == "agent_status":
                        agent = data.get("agent_name", "?")
                        status = data.get("status", "?")
                        elapsed = data.get("elapsed_ms", "")
                        elapsed_str = f" ({elapsed}ms)" if elapsed else ""
                        print(f"  [AGENT] {agent}: {status}{elapsed_str}")
                        if cur:
                            if status == "dispatched":
                                cur.agent_dispatched_t = now
                                cur.agent_dispatched_name = agent
                            elif status == "done" and cur.agent_done_t == 0.0:
                                cur.agent_done_t = now
                                cur.agent_done_name = agent
                    elif msg_type == "playback_state":
                        state = data.get("state", "?")
                        agent = data.get("agent_name", "")
                        extra = f" ({agent})" if agent else ""
                        print(f"  [STATE] {state}{extra}")
                    elif msg_type == "error":
                        code = data.get("code", "?")
                        msg = data.get("message", "?")
                        print(f"  [ERROR] {code}: {msg}")
                    elif msg_type == "interruption":
                        new_gen = data.get("gen_id", "?")
                        print(f"  [INTERRUPT] new gen_id={new_gen}")
                        if gen_id_ref is not None and isinstance(new_gen, int):
                            old_gen = gen_id_ref[0]
                            # Validate monotonicity (modular)
                            diff = (new_gen - old_gen) & 0xFF
                            if diff == 0 or diff >= 128:
                                print(f"  [WARN] gen_id not monotonic: {old_gen} -> {new_gen}")
                            gen_id_ref[0] = new_gen
                        if cur and cur.interrupt_received_t == 0.0:
                            cur.interrupt_received_t = now
                            cur.interrupt_received = True
                        if interrupt_event:
                            interrupt_event.set()
                    else:
                        print(f"  [MSG] {data}")
                except json.JSONDecodeError:
                    print(f"  [RAW] {message[:100]}")
            if stop_event.is_set():
                break
    except websockets.exceptions.ConnectionClosed:
        print("  [WS] Connection closed")


def print_timing_report(tc: TimingCollector):
    print("\n")
    print("=" * 80)
    print("TIMING REPORT")
    print("=" * 80)
    for t in tc.turns:
        origin = t.speech_send_start
        print(f"\n--- Turn {t.turn + 1}: \"{t.utterance}\" ---")
        print(f"  TTS generation:       {_dur(t.tts_start, t.tts_end):>10}")
        print(f"  Speech audio length:  {t.speech_duration_s * 1000:>7.0f}ms")
        print(f"  Speech sending:       {_dur(t.speech_send_start, t.speech_send_end):>10}  (T=0 to {_rel(t.speech_send_end, origin)})")
        print(f"  Silence sending:      {_dur(t.speech_send_end, t.silence_send_end):>10}  (to {_rel(t.silence_send_end, origin)})")
        print(f"  First user partial:   {_rel(t.first_partial_user_t, origin):>10}")
        print(f"  Final user transcript:{_rel(t.final_user_t, origin):>10}")
        print(f"  First mod partial:    {_rel(t.first_partial_mod_t, origin):>10}")
        print(f"  First mod audio:      {_rel(t.first_audio_mod_t, origin):>10}")
        print(f"  Final mod transcript: {_rel(t.final_mod_t, origin):>10}")
        print(f"  Last mod audio:       {_rel(t.last_audio_mod_t, origin):>10}")
        if t.first_partial_mod_t and t.silence_send_end:
            latency = (t.first_partial_mod_t - t.silence_send_end) * 1000
            print(f"  >> Latency (silence-end to first mod response): {latency:>7.0f}ms")
        if t.first_partial_mod_t and t.speech_send_end:
            latency = (t.first_partial_mod_t - t.speech_send_end) * 1000
            print(f"  >> Latency (speech-end to first mod response):  {latency:>7.0f}ms")
        if t.final_mod_t and t.speech_send_start:
            total = (t.final_mod_t - t.speech_send_start) * 1000
            print(f"  >> Total turn time (speech-start to mod final): {total:>7.0f}ms")
        if t.last_audio_mod_t and t.first_audio_mod_t:
            dur = (t.last_audio_mod_t - t.first_audio_mod_t) * 1000
            print(f"  >> Mod audio playback duration:                 {dur:>7.0f}ms")
        if t.agent_dispatched_t:
            print(f"  Agent dispatched:     {_rel(t.agent_dispatched_t, origin):>10}  ({t.agent_dispatched_name})")
        if t.agent_done_t:
            print(f"  Agent done:           {_rel(t.agent_done_t, origin):>10}  ({t.agent_done_name})")
            if t.agent_dispatched_t:
                dur = (t.agent_done_t - t.agent_dispatched_t) * 1000
                print(f"  >> Agent work duration:                         {dur:>7.0f}ms")
        if t.first_agent_audio_t:
            print(f"  First agent audio:    {_rel(t.first_agent_audio_t, origin):>10}  ({t.agent_audio_speaker})")
            print(f"  Last agent audio:     {_rel(t.last_agent_audio_t, origin):>10}")
            if t.last_agent_audio_t and t.first_agent_audio_t:
                dur = (t.last_agent_audio_t - t.first_agent_audio_t) * 1000
                print(f"  >> Agent audio playback duration:               {dur:>7.0f}ms")
        if t.final_mod_text:
            print(f"  Moderator said: \"{t.final_mod_text}\"")
        # Barge-in metrics
        if t.is_barge_in:
            print(f"  [BARGE-IN]")
            if t.barge_in_target_t and t.barge_in_fire_t:
                accuracy = (t.barge_in_fire_t - t.barge_in_target_t) * 1000
                print(f"  Barge-in accuracy:    {accuracy:>+7.0f}ms (target vs actual)")
            if t.interrupt_received_t and t.barge_in_fire_t:
                latency = (t.interrupt_received_t - t.barge_in_fire_t) * 1000
                print(f"  Interrupt latency:    {latency:>7.0f}ms (fire to interrupt)")
            status = "PASS" if t.interrupt_received else "FAIL"
            if t.expect_interrupt:
                print(f"  Expected interrupt:   [{status}]")
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Turn':<5} {'Utterance':<30} {'Speech':>8} {'TTS Gen':>8} {'1st Resp':>9} {'Full Resp':>10} {'Total':>8}")
    print("-" * 80)
    for t in tc.turns:
        speech = f"{t.speech_duration_s * 1000:.0f}ms"
        tts = _dur(t.tts_start, t.tts_end)
        first_resp = _dur(t.speech_send_end, t.first_partial_mod_t) if t.first_partial_mod_t else "n/a"
        full_resp = _dur(t.speech_send_end, t.final_mod_t) if t.final_mod_t else "n/a"
        total = _dur(t.speech_send_start, t.final_mod_t) if t.final_mod_t else "n/a"
        utt = t.utterance[:28] + ".." if len(t.utterance) > 30 else t.utterance
        print(f"{t.turn + 1:<5} {utt:<30} {speech:>8} {tts:>8} {first_resp:>9} {full_resp:>10} {total:>8}")

    # Barge-in summary
    barge_in_turns = [t for t in tc.turns if t.is_barge_in]
    if barge_in_turns:
        print("\n" + "=" * 80)
        print("BARGE-IN SUMMARY")
        print("=" * 80)
        print(f"{'Turn':<5} {'Utterance':<25} {'Fire Delay':>10} {'Int Rcvd':>10} {'Pass':>6}")
        print("-" * 60)
        for t in barge_in_turns:
            utt = t.utterance[:23] + ".." if len(t.utterance) > 25 else t.utterance
            if t.barge_in_target_t and t.barge_in_fire_t:
                fire_delay = f"{(t.barge_in_fire_t - t.barge_in_target_t) * 1000:+.0f}ms"
            else:
                fire_delay = "n/a"
            if t.interrupt_received_t and t.barge_in_fire_t:
                int_rcvd = f"+{(t.interrupt_received_t - t.barge_in_fire_t) * 1000:.0f}ms"
            else:
                int_rcvd = "n/a"
            if t.expect_interrupt:
                status = "YES" if t.interrupt_received else "FAIL"
            else:
                status = "-"
            print(f"{t.turn + 1:<5} {utt:<25} {fire_delay:>10} {int_rcvd:>10} {status:>6}")
    print()


# ---------- Main entry point ----------
async def run_demo(
    case_name: str,
    turns: list[dict],
    final_wait: int = 10,
):
    """Run an automated conversation demo.

    Args:
        case_name: Used for the title and output filename.
        turns: List of turn dicts with keys: text, wait, barge_in,
               delay_from_prev_start, delay_from_response_start,
               expect_interrupt, min_interrupt_delay_ms.
        final_wait: Seconds to wait for trailing agent responses at the end.
    """
    print("=" * 60)
    print(f"NEXUS Voice Agent - {case_name}")
    print("=" * 60)

    tc = TimingCollector()
    recorder = AudioRecorder()

    # Pre-generate TTS for barge-in turns (need audio ready at fire time)
    print("\n[0] Pre-generating TTS for barge-in turns...")
    pregenerated: dict[int, tuple[bytes, float]] = {}  # index -> (pcm, duration)
    for i, turn_data in enumerate(turns):
        if turn_data.get("barge_in"):
            t0 = time.monotonic()
            pcm = generate_tts_audio(turn_data["text"])
            dur = time.monotonic() - t0
            pregenerated[i] = (pcm, dur)
            print(f"    Turn {i+1}: pre-generated {len(pcm)} bytes in {dur*1000:.0f}ms")

    # Step 1: Create session
    print("\n[1] Creating session...")
    tc.session_start = time.monotonic()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BACKEND_URL}/api/v1/sessions")
        resp.raise_for_status()
        session_data = resp.json()
        session_id = session_data["session_id"]
        auth_token = session_data["auth_token"]
        print(f"    Session: {session_id}")

    # Step 2: Connect WebSocket
    print("\n[2] Connecting WebSocket...")
    ws_url = f"{WS_URL}?token={auth_token}"

    async with websockets.connect(ws_url) as ws:
        tc.ws_connected = time.monotonic()
        print(f"    Connected! (took {_dur(tc.session_start, tc.ws_connected)})")

        recorder.start()

        stop_event = asyncio.Event()
        response_event = asyncio.Event()
        interrupt_event = asyncio.Event()
        first_response_event = asyncio.Event()
        gen_id_ref = [0]
        receiver = asyncio.create_task(receive_loop(
            ws, stop_event, response_event, tc, recorder,
            gen_id_ref=gen_id_ref,
            interrupt_event=interrupt_event,
            first_response_event=first_response_event,
        ))

        # Step 3: Start session
        print("\n[3] Starting session...")
        await ws.send(json.dumps({"type": "control", "action": "start"}))
        await asyncio.sleep(2)

        # Step 4: Run conversation
        cancel_event = asyncio.Event()
        send_task: asyncio.Task | None = None
        prev_turn_start_t: float = 0.0
        prev_first_response_t: float = 0.0

        for i, turn_data in enumerate(turns):
            utterance = turn_data["text"]
            delay = turn_data.get("wait", 10)
            is_barge_in = turn_data.get("barge_in", False)
            delay_from_prev_start = turn_data.get("delay_from_prev_start")
            delay_from_response_start = turn_data.get("delay_from_response_start")
            expect_interrupt = turn_data.get("expect_interrupt", False)

            response_event.clear()
            interrupt_event.clear()
            first_response_event.clear()

            turn = tc.new_turn(i, utterance)
            turn.is_barge_in = is_barge_in
            turn.expect_interrupt = expect_interrupt

            if is_barge_in:
                # Compute target fire time and wait for it
                if delay_from_prev_start is not None and prev_turn_start_t > 0:
                    target_t = prev_turn_start_t + delay_from_prev_start
                    turn.barge_in_target_t = target_t
                    wait_secs = max(0, target_t - time.monotonic())
                    if wait_secs > 0:
                        print(f"\n    [BARGE-IN] Waiting {wait_secs:.1f}s to fire...")
                        await asyncio.sleep(wait_secs)
                elif delay_from_response_start is not None:
                    # Wait for first response audio, then delay
                    print(f"\n    [BARGE-IN] Waiting for first response audio...")
                    try:
                        await asyncio.wait_for(first_response_event.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        print(f"    [BARGE-IN] No response audio within 30s, firing anyway")
                    prev_first_response_t = time.monotonic()
                    target_t = prev_first_response_t + delay_from_response_start
                    turn.barge_in_target_t = target_t
                    wait_secs = max(0, target_t - time.monotonic())
                    if wait_secs > 0:
                        await asyncio.sleep(wait_secs)
                else:
                    # Default barge-in: 2s from previous start
                    if prev_turn_start_t > 0:
                        target_t = prev_turn_start_t + 2.0
                        turn.barge_in_target_t = target_t
                        wait_secs = max(0, target_t - time.monotonic())
                        if wait_secs > 0:
                            await asyncio.sleep(wait_secs)

                # Cancel any in-flight send from previous turn
                if send_task and not send_task.done():
                    cancel_event.set()
                    try:
                        await send_task
                    except asyncio.CancelledError:
                        pass
                    cancel_event.clear()
                    cancel_event = asyncio.Event()

                turn.barge_in_fire_t = time.monotonic()

            print(f"\n[USER] >>> {utterance}" + (" [BARGE-IN]" if is_barge_in else ""))
            prev_turn_start_t = time.monotonic()

            # Generate or retrieve pre-generated TTS
            if i in pregenerated:
                pcm_data, tts_dur = pregenerated[i]
                turn.tts_start = time.monotonic() - tts_dur  # approximate
                turn.tts_end = time.monotonic()
            else:
                turn.tts_start = time.monotonic()
                pcm_data = generate_tts_audio(utterance)
                turn.tts_end = time.monotonic()
            turn.speech_duration_s = len(pcm_data) / SAMPLE_RATE / 2
            print(f"    TTS: {len(pcm_data)} bytes ({turn.speech_duration_s:.1f}s audio)")

            send_task = asyncio.create_task(
                send_audio(ws, pcm_data, gen_id_ref[0], turn, recorder, cancel_event)
            )

            if is_barge_in:
                # For barge-in: wait for send to complete, then brief wait for interrupt
                await send_task
                send_task = None
                print(f"    Waiting up to {delay}s for response/interrupt...")
                done, _ = await asyncio.wait(
                    [asyncio.create_task(response_event.wait()),
                     asyncio.create_task(interrupt_event.wait())],
                    timeout=delay,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    print(f"    Got response/interrupt!")
                    await asyncio.sleep(3)
                else:
                    print(f"    Timeout - proceeding to next utterance")
            else:
                # Sequential: wait for send, then wait for response
                await send_task
                send_task = None
                total_frames = len(pcm_data) // CHUNK_SIZE + 1 + int(SILENCE_DURATION * SAMPLE_RATE * 2 / CHUNK_SIZE)
                print(f"    Sent {total_frames} frames (speech {_dur(turn.speech_send_start, turn.speech_send_end)} + silence {_dur(turn.speech_send_end, turn.silence_send_end)})")
                print(f"    Waiting up to {delay}s for response...")
                try:
                    await asyncio.wait_for(response_event.wait(), timeout=delay)
                    print(f"    Got moderator response!")
                    await asyncio.sleep(3)
                except asyncio.TimeoutError:
                    print(f"    Timeout - proceeding to next utterance")

        # Step 5: Wait for final responses
        print(f"\n[5] Waiting {final_wait}s for final responses...")
        await asyncio.sleep(final_wait)

        # Step 6: Stop session
        print("\n[6] Stopping session...")
        await ws.send(json.dumps({"type": "control", "action": "stop"}))
        await asyncio.sleep(2)

        stop_event.set()
        receiver.cancel()
        try:
            await receiver
        except asyncio.CancelledError:
            pass

    # Timing report
    print_timing_report(tc)

    # Save conversation audio
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = str(output_dir / f"{case_name}_{timestamp}.wav")
    print(f"\n[7] Saving conversation audio...")
    recorder.save_wav(output_path)

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)
