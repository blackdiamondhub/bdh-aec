# bdh-aec

Server-side echo cancellation for doorbell talkback on the BlackDiamondHub (beta).

When someone talks to a two-way camera from the hub, the camera's mic picks the
voice back up. bdh-aec runs WebRTC AEC3 on the camera mic (from go2rtc's
`<cam>_low` stream) using the talk audio as the reference, and serves the
cleaned audio as Opus-in-MPEG-TS at `/aec.ts?cam=<cam>`. The cameras app
registers `<cam>_aec` in go2rtc as camera video + that audio.

| Port | Use |
|---|---|
| 11911 | HTTP: `/aec.ts`, `/aec.wav`, and `/api/talk` WebSocket upgrades |
| 11910 | Talk WebSocket proxy to go2rtc `/api/talk` (tees frames as the AEC reference) |

Turn it on in the cameras app settings under **Echo cancellation (beta)** with
`http://<hub-ip>:11911`.

## Deploy

CI publishes `ghcr.io/blackdiamondlodge/bdh-aec:latest` on every push to `main`;
Watchtower picks it up on the hub.

```sh
docker run -d --name bdh-aec --network host --restart unless-stopped \
  -e GO2RTC=http://127.0.0.1:11900 ghcr.io/blackdiamondlodge/bdh-aec:latest
```

Env: `GO2RTC` (go2rtc API URL), `TALK_PORT`, `HTTP_PORT`, `AEC_NS` (noise suppression, 1/0).
