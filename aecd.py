"""bdh-aec: server-side echo cancellation for doorbell talkback.

  kiosk --ws--> :HTTP_PORT/api/talk?src=<cam>_talk --> 127.0.0.1:TALK_PORT --ws--> go2rtc /api/talk
                (frames tee'd as the AEC reference)
  go2rtc http://<this>:HTTP_PORT/aec.ts?cam=<cam>  <--  AEC3(mic=<cam>_low audio, ref)

Normally run by the BDH supervisor as the cameras app's ``aec`` companion.
"""
import asyncio, ctypes, json, logging, math, os, struct, time, urllib.parse, urllib.request
from array import array

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

GO2RTC = os.environ.get("GO2RTC", "http://127.0.0.1:11900")
TALK_PORT = int(os.environ.get("TALK_PORT", "11910"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "11911"))
NS = int(os.environ.get("AEC_NS", "1"))
REF_MAX = 16000 * 2  # cap per-consumer reference FIFO at 1 s (bytes)
FRAME = 320  # 10 ms @ 16 kHz s16le

log = logging.getLogger("aecd")
lib = ctypes.CDLL(os.environ.get("AEC_LIB", "/usr/local/lib/libaec3c.so"))
lib.aec_new.restype = ctypes.c_void_p
lib.aec_new.argtypes = [ctypes.c_int]
lib.aec_process.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
lib.aec_delay_ms.argtypes = [ctypes.c_void_p]
lib.aec_free.argtypes = [ctypes.c_void_p]

# cam -> list of bytearray reference FIFOs (one per active AEC consumer)
refs: dict[str, list] = {}
talking: dict[str, float] = {}  # cam -> last talk frame time


def cam_of(src: str) -> str:
    return src[:-5] if src.endswith("_talk") else src


def rms_db(b: bytes) -> float:
    a = array("h", b)
    if not a:
        return -120.0
    return 10 * math.log10(sum(x * x for x in a) / len(a) / 1073741824 + 1e-12)


async def talk_handler(ws):
    q = urllib.parse.urlparse(ws.request.path)
    src = urllib.parse.parse_qs(q.query).get("src", [""])[0]
    cam = cam_of(src)
    up_url = GO2RTC.replace("http", "ws", 1) + "/api/talk?src=" + urllib.parse.quote(src)
    log.info("talk open src=%s from %s", src, ws.remote_address)
    try:
        async with ws_connect(up_url, max_size=None) as up:
            async def down():
                async for m in up:
                    await ws.send(m)

            t = asyncio.create_task(down())
            try:
                async for m in ws:
                    if isinstance(m, bytes):
                        talking[cam] = time.monotonic()
                        for f in refs.get(cam, ()):
                            f += m
                            if len(f) > REF_MAX:
                                del f[: len(f) - REF_MAX]
                    await up.send(m)
            finally:
                t.cancel()
    except Exception as e:
        log.warning("talk %s: %s", src, e)
        try:
            await ws.send(json.dumps({"type": "error", "value": str(e)}))
        except Exception:
            pass
    log.info("talk close src=%s", src)


def mic_url(cam: str) -> str:
    with urllib.request.urlopen(f"{GO2RTC}/api/streams?src={urllib.parse.quote(cam)}_low", timeout=5) as r:
        s = json.load(r)
    for p in s.get("producers") or []:
        u = p.get("url") or p.get("source") or ""
        if u.startswith("rtsp"):
            return u
    raise RuntimeError(f"no rtsp producer for {cam}_low")


def wav_header() -> bytes:
    return b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", 0xFFFFFFFF)


async def aec_stream(cam: str, write):
    url = await asyncio.to_thread(mic_url, cam)
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
        "-rtsp_transport", "tcp", "-i", url, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE)
    h = lib.aec_new(NS)
    fifo = bytearray()
    refs.setdefault(cam, []).append(fifo)
    out = ctypes.create_string_buffer(FRAME)
    st = dict(n=0, mic=0.0, out=0.0, t=time.monotonic())
    log.info("aec start cam=%s", cam)
    try:
        # The NVR mic takes ~3 s to start; stream real-time silence until it does so the
        # consumer sees audio (and its codec) immediately.
        await write(b"\0" * FRAME * 20)
        first = asyncio.ensure_future(p.stdout.readexactly(FRAME))
        while not first.done():
            await asyncio.wait([first], timeout=0.01)
            if not first.done():
                await write(b"\0" * FRAME)
        mic = first.result()
        while True:
            if mic is None:
                mic = await p.stdout.readexactly(FRAME)
            if len(fifo) >= FRAME:
                ref = bytes(fifo[:FRAME])
                del fifo[:FRAME]
            else:
                ref = b"\0" * FRAME
            lib.aec_process(h, ref, mic, out)
            await write(out.raw)
            # stats while far end is talking (echo windows)
            if time.monotonic() - talking.get(cam, 0) < 0.5:
                st["n"] += 1
                st["mic"] += 10 ** (rms_db(mic) / 10)
                st["out"] += 10 ** (rms_db(out.raw) / 10)
            if time.monotonic() - st["t"] > 5:
                if st["n"]:
                    m, o = st["mic"] / st["n"], st["out"] / st["n"]
                    log.info("cam=%s talk: mic %.1f dB out %.1f dB reduction %.1f dB delay %d ms fifo %d ms",
                             cam, 10 * math.log10(m + 1e-12), 10 * math.log10(o + 1e-12),
                             10 * math.log10((m + 1e-12) / (o + 1e-12)), lib.aec_delay_ms(h), len(fifo) // 32)
                st.update(n=0, mic=0.0, out=0.0, t=time.monotonic())
            mic = None
    except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError) as e:
        log.info("aec stop cam=%s: %r", cam, e)
    finally:
        refs[cam] = [f for f in refs[cam] if f is not fifo]
        lib.aec_free(h)
        if p.returncode is None:
            p.kill()
        await p.wait()


async def http_handler(reader, writer):
    try:
        raw = await reader.readline()
        head = [raw]
        while (h := await reader.readline()) not in (b"\r\n", b"\n", b""):
            head.append(h)
        head.append(b"\r\n")
        line = raw.decode(errors="replace")
        path = line.split(" ")[1] if " " in line else "/"
        q = urllib.parse.urlparse(path)
        if q.path == "/api/talk" and any(h.lower().startswith(b"upgrade:") and b"websocket" in h.lower() for h in head):
            # One port for everything: hand talk WebSocket upgrades to the WS server.
            ur, uw = await asyncio.open_connection("127.0.0.1", TALK_PORT)
            uw.write(b"".join(head))

            async def pipe(r, w):
                try:
                    while b := await r.read(65536):
                        w.write(b)
                        await w.drain()
                finally:
                    w.close()

            await asyncio.gather(pipe(reader, uw), pipe(ur, writer), return_exceptions=True)
            return
        cam = urllib.parse.parse_qs(q.query).get("cam", [""])[0]
        if q.path not in ("/aec.wav", "/aec.ts") or not cam:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return
        ts = q.path == "/aec.ts"
        ctype = b"video/mp2t" if ts else b"audio/wav"
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: " + ctype + b"\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
        if not ts:
            writer.write(wav_header())

            async def write(b):
                writer.write(b)
                await writer.drain()

            await aec_stream(cam, write)
            return
        # Opus in MPEG-TS: go2rtc demuxes it natively (no ffmpeg/RTSP needed inside go2rtc).
        # No input probing: the PCM format is fixed, and probing it held the first audio
        # (and so go2rtc's start of the whole stream, video included) back by ~3 s.
        enc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-probesize", "32", "-analyzeduration", "0", "-fflags", "nobuffer",
            "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "-",
            "-c:a", "libopus", "-b:a", "48k", "-application", "voip", "-frame_duration", "20",
            "-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", "-flush_packets", "1", "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

        async def pump():
            while b := await enc.stdout.read(4096):
                writer.write(b)
                await writer.drain()

        async def write(b):
            enc.stdin.write(b)
            await enc.stdin.drain()

        pt = asyncio.create_task(pump())
        at = asyncio.create_task(aec_stream(cam, write))
        try:
            await asyncio.wait([pt, at], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (pt, at):
                t.cancel()
            await asyncio.gather(pt, at, return_exceptions=True)
            if enc.returncode is None:
                enc.kill()
            await enc.wait()
    except Exception as e:
        log.warning("http: %r", e)
    finally:
        writer.close()


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await asyncio.start_server(http_handler, "0.0.0.0", HTTP_PORT)
    async with ws_serve(talk_handler, "127.0.0.1", TALK_PORT, max_size=None, compression=None):
        log.info("listening talk=%d http=%d go2rtc=%s ns=%d", TALK_PORT, HTTP_PORT, GO2RTC, NS)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
