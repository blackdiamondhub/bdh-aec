# bdh-aec

Server-side echo cancellation for doorbell talkback on the BlackDiamondHub (beta).

When someone talks to a two-way camera from the hub, the camera's mic picks the
voice back up. bdh-aec runs WebRTC AEC3 on the camera mic (from go2rtc's
`<cam>_low` stream) using the talk audio as the reference, and serves the
cleaned audio as Opus-in-MPEG-TS at `/aec.ts?cam=<cam>`. The cameras app
registers `<cam>_aec` in go2rtc as camera video + that audio.

## How it's deployed

It's a **platform companion**, like go2rtc: the cameras app declares
`companions=("go2rtc", "aec")` and `bdh-supervisor` runs this image next to it
(host network, read-only, uid 10001) and hands the app its address as
`BDH_AEC_URL`. Nothing to install by hand: installing or updating the cameras
app brings it along.

CI publishes `ghcr.io/blackdiamondhub/bdh-aec:latest` (and `:sha-<sha>`) on
every push to `main`.

| Env | Default | |
|---|---|---|
| `GO2RTC` | `http://127.0.0.1:11900` | go2rtc API |
| `HTTP_HOST` | `0.0.0.0` | Address the HTTP port binds to. The supervisor sets `127.0.0.1`: go2rtc and the cameras app (which checks who may talk and relays the kiosk's talk websocket) are on the same host, and nothing else should reach it |
| `HTTP_PORT` | `11911` | `/aec.ts`, `/aec.wav`, and `/api/talk` WebSocket upgrades |
| `TALK_PORT` | `11910` | Internal talk WebSocket proxy to go2rtc `/api/talk`, loopback only |
| `AEC_NS` | `1` | Noise suppression on/off |

For testing outside the supervisor:

```sh
docker run --rm --network host -e GO2RTC=http://127.0.0.1:11900 ghcr.io/blackdiamondhub/bdh-aec:latest
```
