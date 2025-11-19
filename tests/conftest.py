"""Pytest fixtures and fallbacks for optional dependencies."""

from __future__ import annotations

import builtins
import math
import struct
import sys
import types
from pathlib import Path
from typing import Iterable, List, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_numpy_stub() -> None:
    if "numpy" in sys.modules:
        return

    class _DType:
        def __init__(self, name: str, itemsize: int):
            self.name = name
            self.itemsize = itemsize

        def __repr__(self) -> str:
            return f"dtype('{self.name}')"

    class _NDArray:
        def __init__(self, data: Iterable[float], dtype: _DType):
            self._data: List[float] = list(data)
            self.dtype = dtype

        @property
        def size(self) -> int:
            return len(self._data)

        def __len__(self) -> int:
            return len(self._data)

        def __iter__(self):
            return iter(self._data)

        def __getitem__(self, item):
            if isinstance(item, slice):
                return _NDArray(self._data[item], dtype=self.dtype)
            return self._data[item]

        def __setitem__(self, item, value):
            if isinstance(item, slice):
                self._data[item] = _to_list(value)
            else:
                self._data[item] = value

        def __eq__(self, other):
            return [value == other for value in self._data]

        def max(self):
            return max(self._data) if self._data else 0.0

        def astype(self, dtype: _DType) -> "_NDArray":
            if dtype is float32:
                return _NDArray((float(v) for v in self._data), dtype=float32)
            if dtype is int16:
                return _NDArray((int(round(v)) for v in self._data), dtype=int16)
            raise TypeError(f"Unsupported dtype conversion to {dtype!r}")

        def __mul__(self, other: float) -> "_NDArray":
            return _NDArray((v * other for v in self._data), dtype=self.dtype)

        def __rmul__(self, other: float) -> "_NDArray":
            return self.__mul__(other)

        def __truediv__(self, other: float) -> "_NDArray":
            return _NDArray((v / other for v in self._data), dtype=self.dtype)

        def tobytes(self) -> bytes:
            if self.dtype is int16:
                fmt = f"<{len(self._data)}h"
                return struct.pack(fmt, *[int(round(v)) for v in self._data])
            if self.dtype is float32:
                fmt = f"<{len(self._data)}f"
                return struct.pack(fmt, *[float(v) for v in self._data])
            raise TypeError(f"Unsupported dtype for tobytes(): {self.dtype!r}")

    def _to_list(values) -> List[float]:
        if isinstance(values, _NDArray):
            return list(values._data)
        if isinstance(values, Sequence):
            return list(values)
        return list(values)

    float32 = _DType("float32", 4)
    int16 = _DType("int16", 2)

    def _array(values, dtype: _DType = float32) -> _NDArray:
        return _NDArray(_to_list(values), dtype=dtype)

    def _empty(length: int, dtype: _DType = float32) -> _NDArray:
        return _NDArray([0.0] * int(length), dtype=dtype)

    def _zeros(length: int, dtype: _DType = float32) -> _NDArray:
        fill_value = 0.0 if dtype is float32 else 0
        return _full(length, fill_value, dtype=dtype)

    def _full(length: int, value: float, dtype: _DType = float32) -> _NDArray:
        return _NDArray([value] * int(length), dtype=dtype)

    def _concatenate(chunks: Sequence[_NDArray]) -> _NDArray:
        data: List[float] = []
        dtype = chunks[0].dtype if chunks else float32
        for chunk in chunks:
            data.extend(_to_list(chunk))
        return _NDArray(data, dtype=dtype)

    def _clip(values, min_value: float, max_value: float) -> _NDArray:
        data = _to_list(values)
        clipped = [max(min_value, min(max_value, v)) for v in data]
        dtype = values.dtype if isinstance(values, _NDArray) else float32
        return _NDArray(clipped, dtype=dtype)

    def _frombuffer(buffer: bytes, dtype: _DType):
        if dtype is not int16:
            raise TypeError("frombuffer stub only supports int16")
        length = len(buffer) // int16.itemsize
        if length == 0:
            return _NDArray([], dtype=int16)
        fmt = f"<{length}h"
        values = struct.unpack(fmt, buffer[: length * int16.itemsize])
        return _NDArray(values, dtype=int16)

    def _all(values) -> bool:
        return builtins.all(values)

    stub = types.ModuleType("numpy")
    stub.float32 = float32
    stub.int16 = int16
    stub.bool_ = bool
    stub.ndarray = _NDArray
    stub.array = _array
    stub.empty = _empty
    stub.zeros = _zeros
    stub.full = _full
    stub.concatenate = _concatenate
    stub.clip = _clip
    stub.frombuffer = _frombuffer
    stub.all = _all
    stub.isscalar = lambda obj: isinstance(obj, (int, float, bool))

    sys.modules["numpy"] = stub


try:
    import numpy  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    _install_numpy_stub()


def _install_audioop_stub() -> None:
    if "audioop" in sys.modules:
        return

    def _rms(fragment: bytes, width: int) -> int:
        if width != 2:
            raise ValueError("audioop stub only supports 16-bit samples")
        if len(fragment) % width:
            fragment = fragment[: len(fragment) - (len(fragment) % width)]
        count = len(fragment) // width
        if count == 0:
            return 0
        fmt = f"<{count}h"
        samples = struct.unpack(fmt, fragment)
        mean = sum(sample * sample for sample in samples) / count
        return int(math.sqrt(mean))

    def _ratecv(data, width, nchannels, inrate, outrate, state=None):
        if width != 2:
            raise ValueError("audioop stub only supports 16-bit samples")
        return data, state

    stub = types.ModuleType("audioop")
    stub.rms = _rms
    stub.ratecv = _ratecv
    sys.modules["audioop"] = stub


try:
    import audioop  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    _install_audioop_stub()


def _install_gnuradio_stub() -> None:
    if "gnuradio" in sys.modules:
        return

    class _DummyBlock:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _DummyGraph(_DummyBlock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.connections = []

        def connect(self, *blocks):
            self.connections.append(blocks)

        def run(self):
            return None

    analog_module = types.ModuleType("gnuradio.analog")
    analog_module.GR_SIN_WAVE = 0
    analog_module.sig_source_f = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    analog_module.frequency_modulator_fc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)

    blocks_module = types.ModuleType("gnuradio.blocks")
    blocks_module.add_ff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.add_cc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.multiply_ff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.multiply_const_ff = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.multiply_const_cc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.rotator_cc = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.vector_source_f = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)
    blocks_module.vector_sink_f = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)

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
    gr_module.sizeof_float = 1

    osmosdr_module = types.ModuleType("osmosdr")
    osmosdr_module.sink = lambda *args, **kwargs: _DummyBlock(*args, **kwargs)

    stubs = {
        "gnuradio": types.ModuleType("gnuradio"),
        "gnuradio.analog": analog_module,
        "gnuradio.blocks": blocks_module,
        "gnuradio.filter": filter_module,
        "gnuradio.filter.firdes": firdes_module,
        "gnuradio.filter.window": window_module,
        "gnuradio.gr": gr_module,
        "osmosdr": osmosdr_module,
    }
    stubs["gnuradio"].analog = analog_module
    stubs["gnuradio"].blocks = blocks_module
    stubs["gnuradio"].filter = filter_module
    stubs["gnuradio"].gr = gr_module

    sys.modules.update(stubs)


try:
    import gnuradio  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    _install_gnuradio_stub()
