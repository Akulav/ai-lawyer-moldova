"""Memory use for the Index page: system RAM, the AI engine's RAM, and dedicated GPU memory (VRAM).

VRAM comes from the Windows "GPU Adapter Memory" performance counters, which work for any GPU vendor,
and the card's size from the display driver's registry entry. Readings are cached for a few seconds.
"""
import ctypes
import time
from ctypes import wintypes

import psutil

from .config import OLLAMA_DIR

_cache: dict = dict(at=0.0, value=None)
TTL = 5


class _Value(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]


def _vram_used() -> int | None:
    """Bytes of dedicated memory in use on the busiest GPU, across all processes."""
    try:
        pdh = ctypes.windll.pdh
    except (AttributeError, OSError):
        return None
    query, counter = wintypes.HANDLE(), wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
        return None
    try:
        if pdh.PdhAddEnglishCounterW(query, r"\GPU Adapter Memory(*)\Dedicated Usage", 0, ctypes.byref(counter)) != 0:
            return None
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        PDH_FMT_DOUBLE, PDH_MORE_DATA = 0x200, 0x800007D2
        rc = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if rc & 0xFFFFFFFF != PDH_MORE_DATA:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        return int(max((items[i].FmtValue.doubleValue for i in range(count.value)), default=0))
    finally:
        pdh.PdhCloseQuery(query)


def _vram_total() -> tuple[int | None, str | None]:
    """Size and name of the largest display adapter, from the driver's registry entry."""
    try:
        import winreg
    except ImportError:
        return None, None
    best = (None, None)
    key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        size = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
                        name = winreg.QueryValueEx(k, "DriverDesc")[0]
                except OSError:
                    continue
                if isinstance(size, int) and size > (best[0] or 0):
                    best = (size, name)
    except OSError:
        pass
    return best


def _engine_ram() -> int:
    """Resident memory of this app's own Ollama processes, including the model runner."""
    own = str(OLLAMA_DIR).lower()
    total = 0
    for p in psutil.process_iter(["exe", "memory_info"]):
        if (p.info["exe"] or "").lower().startswith(own) and p.info["memory_info"]:
            total += p.info["memory_info"].rss
    return total


def memory() -> dict:
    if time.time() - _cache["at"] < TTL and _cache["value"]:
        return _cache["value"]
    gb = lambda b: None if b is None else round(b / 2**30, 1)
    vm = psutil.virtual_memory()
    vram_total, gpu = _vram_total()
    value = dict(ram_total=gb(vm.total), ram_used=gb(vm.total - vm.available), engine_ram=gb(_engine_ram()),
                 vram_total=gb(vram_total), vram_used=gb(_vram_used()), gpu=gpu)
    _cache.update(at=time.time(), value=value)
    return value
