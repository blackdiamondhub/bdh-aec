// Minimal C ABI over WebRTC AEC3 (libwebrtc-audio-processing 1.x). 16 kHz mono, 10 ms frames.
#include <modules/audio_processing/include/audio_processing.h>
#include <cstdint>

extern "C" {
void* aec_new(int ns) {
  auto apm = webrtc::AudioProcessingBuilder().Create();
  webrtc::AudioProcessing::Config c;
  c.echo_canceller.enabled = true;
  c.echo_canceller.mobile_mode = false;
  c.high_pass_filter.enabled = true;
  c.noise_suppression.enabled = ns != 0;
  c.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kModerate;
  apm->ApplyConfig(c);
  return apm;
}
// ref, mic, out: 160 samples each. Returns 0 on success.
int aec_process(void* h, const int16_t* ref, const int16_t* mic, int16_t* out) {
  auto apm = static_cast<webrtc::AudioProcessing*>(h);
  webrtc::StreamConfig sc(16000, 1);
  int16_t tmp[160];
  int r = apm->ProcessReverseStream(ref, sc, sc, tmp);
  if (r) return r;
  apm->set_stream_delay_ms(0);
  return apm->ProcessStream(mic, sc, sc, out);
}
int aec_delay_ms(void* h) {
  auto s = static_cast<webrtc::AudioProcessing*>(h)->GetStatistics();
  return s.delay_ms ? *s.delay_ms : -1;
}
void aec_free(void* h) { delete static_cast<webrtc::AudioProcessing*>(h); }
}