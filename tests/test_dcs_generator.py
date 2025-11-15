import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_stub_modules():
    if 'multich_nbfm_tx' in sys.modules:
        del sys.modules['multich_nbfm_tx']

    analog_module = types.ModuleType('gnuradio.analog')
    analog_module.sig_source_f = lambda *args, **kwargs: None
    analog_module.GR_SIN_WAVE = 0

    blocks_module = types.ModuleType('gnuradio.blocks')
    blocks_module.add_ff = lambda *args, **kwargs: None
    blocks_module.add_cc = lambda *args, **kwargs: None
    blocks_module.multiply_const_ff = lambda *args, **kwargs: None
    blocks_module.multiply_const_cc = lambda *args, **kwargs: None
    blocks_module.rotator_cc = lambda *args, **kwargs: None

    filter_module = types.ModuleType('gnuradio.filter')
    filter_module.fir_filter_fff = lambda *args, **kwargs: None
    filter_module.fir_filter_ccf = lambda *args, **kwargs: None
    filter_module.rational_resampler_fff = lambda *args, **kwargs: None
    filter_module.rational_resampler_ccc = lambda *args, **kwargs: None

    firdes_module = types.ModuleType('gnuradio.filter.firdes')
    firdes_module.low_pass = lambda *args, **kwargs: None

    window_module = types.ModuleType('gnuradio.filter.window')
    window_module.WIN_HAMMING = 0

    gr_module = types.ModuleType('gnuradio.gr')
    gr_module.sync_block = object
    gr_module.hier_block2 = object
    gr_module.top_block = object
    gr_module.io_signature = lambda *args, **kwargs: None
    gr_module.basic_block = object

    osmosdr_module = types.ModuleType('osmosdr')
    osmosdr_module.sink = lambda *args, **kwargs: None

    audioop_module = types.ModuleType('audioop')
    audioop_module.ratecv = lambda data, width, chan, inrate, outrate, state=None: (data, state)

    numpy_module = types.ModuleType('numpy')
    numpy_module.float32 = 'float32'
    numpy_module.int16 = 'int16'
    numpy_module.ndarray = list
    numpy_module.empty = lambda *args, **kwargs: []
    numpy_module.frombuffer = lambda buffer, dtype=None: []

    stubs = {
        'gnuradio': types.ModuleType('gnuradio'),
        'gnuradio.analog': analog_module,
        'gnuradio.blocks': blocks_module,
        'gnuradio.filter': filter_module,
        'gnuradio.filter.firdes': firdes_module,
        'gnuradio.filter.window': window_module,
        'gnuradio.gr': gr_module,
        'osmosdr': osmosdr_module,
        'audioop': audioop_module,
        'numpy': numpy_module,
    }
    stubs['gnuradio'].analog = analog_module
    stubs['gnuradio'].blocks = blocks_module
    stubs['gnuradio'].filter = filter_module
    stubs['gnuradio'].gr = gr_module

    sys.modules.update(stubs)


_install_stub_modules()
multich = importlib.import_module('multich_nbfm_tx')


def test_parse_code_supports_variants():
    parse = multich.DCSGenerator._parse_code
    assert parse('023') == ('023', False)
    assert parse('D023N') == ('023', False)
    assert parse('023I') == ('023', True)


def test_build_pattern_matches_cdcss_reference():
    build = multich.DCSGenerator._build_pattern
    assert build('023', False) == [
        0, 0, 0, 0, 1, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 0, 1, 0,
    ]
    # Inverted patterns should be the logical complement.
    expected = [
        1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0, 1, 1, 0, 0,
    ]
    assert build('606', False) == expected
    assert build('606', True) == [1 - b for b in expected]
