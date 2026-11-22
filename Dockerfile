FROM debian:trixie-slim AS build
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends g++ pkg-config libwebrtc-audio-processing-dev >/dev/null
COPY aec3c.cc /src/
RUN g++ -O2 -std=c++17 -shared -fPIC /src/aec3c.cc -o /src/libaec3c.so $(pkg-config --cflags --libs webrtc-audio-processing-1)

FROM debian:trixie-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3 python3-numpy python3-websockets ffmpeg libwebrtc-audio-processing-1-3 >/dev/null && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/libaec3c.so /usr/local/lib/
COPY aecd.py /app/
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/aecd.py"]