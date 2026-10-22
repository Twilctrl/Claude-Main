"""CPython stand-ins for the MicroPython features the device code uses.

install() adds a fake `micropython` module whose @viper decorator turns
ptr8 / ptr16 / ptr32 arguments into memoryviews with the same signedness as a
32-bit MicroPython (unsigned 8 and 16-bit, signed 32-bit), and puts ptr8 /
ptr16 / ptr32 into builtins for the annotations and casts. Integer overflow
is not emulated, but writing a value outside int32 into a ptr32 raises, so
an overflow in the engine would still show up here. Does nothing on MicroPython.
"""

import sys

_FMT = {"ptr8": "B", "ptr16": "H", "ptr32": "i"}


def _view(obj, fmt):
    mv = obj if isinstance(obj, memoryview) else memoryview(obj)
    if mv.format != "B":
        mv = mv.cast("B")
    return mv if fmt == "B" else mv.cast(fmt)


def _ptr(name):
    fmt = _FMT[name]

    def cast(obj):
        return _view(obj, fmt)

    cast.__name__ = name
    return cast


def viper(fn):
    import functools

    names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
    ann = fn.__annotations__
    conv = [(i, _FMT[ann[n].__name__]) for i, n in enumerate(names)
            if n in ann and getattr(ann[n], "__name__", "") in _FMT]

    @functools.wraps(fn)
    def wrapper(*args):
        args = list(args)
        for i, fmt in conv:
            args[i] = _view(args[i], fmt)
        return fn(*args)

    return wrapper


def install():
    if sys.implementation.name == "micropython":
        return
    import builtins
    import types

    mod = types.ModuleType("micropython")
    mod.viper = viper
    mod.native = lambda fn: fn
    mod.const = lambda v: v
    sys.modules["micropython"] = mod
    for name in _FMT:
        setattr(builtins, name, _ptr(name))
