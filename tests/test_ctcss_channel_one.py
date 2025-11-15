import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _DummyBlock:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _DummySigSource(_DummyBlock):
    def __init__(self, sample_rate, wave_type, frequency, amplitude, offset):
        super().__init__(sample_rate, wave_type, frequency, amplitude, offset)
        self.sample_rate = sample_rate
        self.wave_type = wave_type
        self.frequency = frequency
        self.amplitude = amplitude
        self.offset = offset


class _DummyGraph:
    def __init__(self, *args, **kwargs):
        self.connections = []

    def connect(self, *blocks):
        self.connections.append(blocks)


class _DummySink:
    def __init__(self):
        self.settings = {}

    def set_sample_rate(self, value):
        self.settings["sample_rate"] = value

    def set_center_freq(self, value):
        self.settings["center_freq"] = value

    def set_gain(self, value):
        self.settings["gain"] = value

    def set_if_gain(self, value):
        self.settings["if_gain"] = value

    def set_bb_gain(self, value):
        self.settings["bb_gain"] = value

    def set_antenna(self, value):
        self.settings["antenna"] = value

    def set_bandwidth(self, value):
        self.settings["bandwidth"] = value


class _DummyQueuedSource:
    def __init__(self, wav_paths, repeat=True, target_sample_rate=None):
        self.wav_paths = list(wav_paths)
        self.repeat = repeat
        self.target_sample_rate = target_sample_rate
        self.sample_rate = 48_000


_NUMPY_STUB = types.ModuleType("numpy")
_NUMPY_STUB.float32 = "float32"
_NUMPY_STUB.int16 = "int16"
_NUMPY_STUB.ndarray = list
_NUMPY_STUB.empty = lambda *args, **kwargs: []
_NUMPY_STUB.frombuffer = lambda buffer, dtype=None: []
_NUMPY_STUB.isscalar = lambda obj: isinstance(obj, (int, float, complex, bool))
_NUMPY_STUB.bool_ = bool


class _StubModules:
    def __init__(self):
        self.modules = {}

    def install(self):
        analog_module = types.ModuleType("gnuradio.analog")
        analog_module.GR_SIN_WAVE = 0
        analog_module.sig_source_f = lambda *args: _DummySigSource(*args)
        analog_module.frequency_modulator_fc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)

        blocks_module = types.ModuleType("gnuradio.blocks")
        blocks_module.add_ff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        blocks_module.add_cc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        blocks_module.multiply_const_ff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        blocks_module.multiply_const_cc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        blocks_module.rotator_cc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)

        filter_module = types.ModuleType("gnuradio.filter")
        filter_module.fir_filter_fff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        filter_module.fir_filter_ccf = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        filter_module.rational_resampler_fff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
        filter_module.rational_resampler_ccc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)

        firdes_module = types.ModuleType("gnuradio.filter.firdes")
        firdes_module.low_pass = lambda *args, **kwargs: (args, kwargs)

        window_module = types.ModuleType("gnuradio.filter.window")
        window_module.WIN_HAMMING = 0

        gr_module = types.ModuleType("gnuradio.gr")
        gr_module.sync_block = _DummyGraph
        gr_module.hier_block2 = _DummyGraph
        gr_module.top_block = _DummyGraph
        gr_module.io_signature = lambda *args, **kwargs: None
        gr_module.basic_block = _DummyBlock
        gr_module.sizeof_gr_complex = 1

        osmosdr_module = types.ModuleType("osmosdr")
        osmosdr_module.sink = lambda *args, **kwargs: _DummySink()

        audioop_module = types.ModuleType("audioop")
        audioop_module.ratecv = lambda data, width, chan, inrate, outrate, state=None: (data, state)

        stubs = {
            "gnuradio": types.ModuleType("gnuradio"),
            "gnuradio.analog": analog_module,
            "gnuradio.blocks": blocks_module,
            "gnuradio.filter": filter_module,
            "gnuradio.filter.firdes": firdes_module,
            "gnuradio.filter.window": window_module,
            "gnuradio.gr": gr_module,
            "osmosdr": osmosdr_module,
            "audioop": audioop_module,
        }
        stubs["gnuradio"].analog = analog_module
        stubs["gnuradio"].blocks = blocks_module
        stubs["gnuradio"].filter = filter_module
        stubs["gnuradio"].gr = gr_module

        sys.modules.pop("numpy", None)
        sys.modules.update(stubs)
        sys.modules["numpy"] = _NUMPY_STUB
        self.modules = stubs


def _import_module():
    if "multich_nbfm_tx" in sys.modules:
        del sys.modules["multich_nbfm_tx"]

    _StubModules().install()
    module = importlib.import_module("multich_nbfm_tx")
    module.QueuedAudioSource = _DummyQueuedSource
    return module


def test_ctcss_only_on_first_channel():
    multich = _import_module()

    tx = multich.MultiNBFMTx(
        device="hackrf",
        center_freq=462.6e6,
        file_groups=[[Path("ch1.wav")], [Path("ch2.wav")]],
        offsets=[-1250.0, 1250.0],
        ctcss_tones=[67.0, None],
        dcs_codes=[None, None],
    )

    ctcss_source = tx.channels[0].ctcss_src
    assert isinstance(ctcss_source, _DummySigSource)
    assert ctcss_source.frequency == pytest.approx(67.0)
    assert ctcss_source.amplitude == pytest.approx(0.25)
    assert tx.channels[1].ctcss_src is None


def test_ctcss_second_channel_rejected():
    multich = _import_module()

    with pytest.raises(ValueError):
        multich.MultiNBFMTx(
            device="hackrf",
            center_freq=462.6e6,
            file_groups=[[Path("ch1.wav")], [Path("ch2.wav")]],
            offsets=[-1250.0, 1250.0],
            ctcss_tones=[67.0, 71.9],
            dcs_codes=[None, None],
        )
