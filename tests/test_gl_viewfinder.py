# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""One import cache per camera, shared by viewfinder and mirrors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gl_viewfinder = pytest.importorskip("camlab.gl_viewfinder")


class FakeBuffer:
    made = 0

    def __init__(self, display, completed_request, max_texture_size):
        FakeBuffer.made += 1
        self.texture = FakeBuffer.made
        self.luma = 100 + FakeBuffer.made


def request(name: str):
    return SimpleNamespace(request=name)


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch):
    deleted: list[int] = []
    monkeypatch.setattr(FakeBuffer, "made", 0)
    monkeypatch.setattr(gl_viewfinder, "_Buffer", FakeBuffer)
    monkeypatch.setattr(gl_viewfinder, "glDeleteTextures", lambda n, ids: deleted.extend(ids))
    monkeypatch.setattr(gl_viewfinder, "eglGetCurrentDisplay", lambda: "display")
    monkeypatch.setattr(gl_viewfinder, "glGetIntegerv", lambda pname: 4096)
    picam2 = SimpleNamespace(stop_count=0)
    return gl_viewfinder._DisplayStream(picam2), picam2, deleted


def test_imports_each_request_once(stream):
    display_stream, _, deleted = stream
    first = display_stream.buffer_for(request("a"))
    assert display_stream.buffer_for(request("a")) is first
    assert display_stream.buffer_for(request("b")) is not first
    assert FakeBuffer.made == 2
    assert deleted == []


def test_reconfigure_drops_every_import(stream):
    display_stream, picam2, deleted = stream
    old = [display_stream.buffer_for(request(name)) for name in ("a", "b")]
    picam2.stop_count += 1
    new = display_stream.buffer_for(request("c"))
    assert sorted(deleted) == sorted(t for b in old for t in (b.texture, b.luma))
    assert list(display_stream.buffers.values()) == [new]
