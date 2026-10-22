import asyncio
from datetime import timedelta
import importlib.util
import os
import sys
import types


class _Exc:
    class ValueError(Exception):
        pass


fake_bot = types.SimpleNamespace()
fake_bot.auto_ready = {}
fake_bot.Exc = _Exc
sys.modules['bot'] = fake_bot

core_utils = types.SimpleNamespace()
core_utils.seconds_to_str = lambda s: str(s)
core_utils.find = lambda predicate, seq: next((x for x in seq if predicate(x)), None)
sys.modules['core.utils'] = core_utils
sys.modules['core.database'] = types.SimpleNamespace(db=types.SimpleNamespace())
sys.modules['core.config'] = types.SimpleNamespace(cfg=types.SimpleNamespace(HELP=''))
sys.modules['bot.commands'] = types.ModuleType('bot.commands')

misc_path = os.path.join(os.path.dirname(__file__), '..', 'bot', 'commands', 'misc.py')
misc_path = os.path.normpath(misc_path)
spec = importlib.util.spec_from_file_location('bot.commands.misc', misc_path)
misc_mod = importlib.util.module_from_spec(spec)
sys.modules['bot.commands.misc'] = misc_mod
spec.loader.exec_module(misc_mod)
auto_ready = misc_mod.auto_ready


class DummyCfg:
    def __init__(self, max_auto_ready):
        self.max_auto_ready = max_auto_ready


class DummyQC:
    def __init__(self, max_auto_ready=3600):
        self.cfg = DummyCfg(max_auto_ready)
        self.gt = lambda s: s


class DummyAuthor:
    def __init__(self, user_id):
        self.id = user_id


class DummyCtx:
    def __init__(self, qc, author):
        self.qc = qc
        self.author = author
        self.success_called = False
        self.success_msg = None

    async def success(self, msg):
        self.success_called = True
        self.success_msg = msg


def test_auto_ready_enable_disable(monkeypatch):
    fixed = 1_700_000_000
    monkeypatch.setattr(misc_mod, 'time', lambda: fixed)

    qc = DummyQC(max_auto_ready=3600)
    author = DummyAuthor(12345)
    ctx = DummyCtx(qc, author)

    fake_bot.auto_ready.pop(author.id, None)

    asyncio.run(auto_ready(ctx, timedelta(seconds=60)))

    assert author.id in fake_bot.auto_ready
    assert isinstance(fake_bot.auto_ready[author.id], int)
    assert fake_bot.auto_ready[author.id] == int(fixed) + 60
    assert ctx.success_called

    ctx.success_called = False
    asyncio.run(auto_ready(ctx, timedelta(seconds=60)))
    assert author.id not in fake_bot.auto_ready
    assert ctx.success_called


def test_auto_ready_exceeds_max():
    qc = DummyQC(max_auto_ready=30)
    author = DummyAuthor(222)
    ctx = DummyCtx(qc, author)

    try:
        asyncio.run(auto_ready(ctx, timedelta(seconds=60)))
        assert False, 'Expected ValueError'
    except _Exc.ValueError:
        pass
