import numpy as np
from gnuradio import blocks, gr

from multich_nbfm_tx import AudioActivityGate


def _run_gate(samples, **gate_kwargs):
    tb = gr.top_block()
    src = blocks.vector_source_f(samples, False)
    gate = AudioActivityGate(sample_rate=48000, **gate_kwargs)
    sink = blocks.vector_sink_f()
    tb.connect(src, gate)
    tb.connect(gate, sink)
    tb.run()
    return np.array(sink.data(), dtype=np.float32)


def test_gate_remains_low_for_silence():
    samples = [0.0] * 1000
    out = _run_gate(samples, open_threshold=0.01, close_threshold=0.005)
    assert np.all(out == 0.0)


def test_gate_opens_with_audio_and_releases():
    samples = ([0.0] * 200) + ([0.6] * 400) + ([0.0] * 400)
    out = _run_gate(
        samples,
        open_threshold=0.05,
        close_threshold=0.02,
        attack_ms=1.0,
        release_ms=20.0,
    )
    # Gate should open while the signal is active
    middle = out[200:600]
    assert middle.max() == 1.0
    # Ensure the gate eventually closes again after silence
    tail = out[600:]
    assert tail[-1] == 0.0
