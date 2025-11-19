import numpy as np

from multich_nbfm_tx import AudioActivityGate


def _run_gate(samples, **gate_kwargs):
    gate = AudioActivityGate(sample_rate=48000, **gate_kwargs)
    inputs = [np.array(samples, dtype=np.float32)]
    outputs = [np.empty(len(samples), dtype=np.float32)]
    gate.work(inputs, outputs)
    return outputs[0]


def test_gate_remains_low_for_silence():
    samples = [0.0] * 1000
    out = _run_gate(samples, open_threshold=0.01, close_threshold=0.005)
    assert np.all(out == 0.0)


def test_gate_opens_with_audio_and_releases():
    trailing_silence = [0.0] * 5000
    samples = ([0.0] * 200) + ([0.6] * 400) + trailing_silence
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
    tail = out[-len(trailing_silence) :]
    assert tail[-1] == 0.0
