# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""One import cache and one peaking guide per camera, shared by viewfinder and mirrors."""

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
    made = iter(range(1, 100))
    monkeypatch.setattr(FakeBuffer, "made", 0)
    monkeypatch.setattr(gl_viewfinder, "_Buffer", FakeBuffer)
    monkeypatch.setattr(gl_viewfinder, "glDeleteTextures", lambda n, ids: deleted.extend(ids))
    monkeypatch.setattr(gl_viewfinder, "eglGetCurrentDisplay", lambda: "display")
    monkeypatch.setattr(gl_viewfinder, "glGetIntegerv", lambda pname: 4096)
    monkeypatch.setattr(gl_viewfinder, "glGenTextures", lambda n: next(made))
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


@pytest.mark.parametrize(
    ("source", "expect"),
    [
        ((1788, 1006), (894, 503)),
        ((698, 392), (349, 196)),
        ((1006, 1788), (503, 894)),  # quarter turn, grid follows the display
        ((1, 1), (1, 1)),
    ],
)
def test_guide_size_halves_the_source(source, expect):
    assert gl_viewfinder._guide_size(*source) == expect


def test_heads_showing_one_orientation_share_the_guide(stream):
    display_stream, _, _ = stream
    panel = display_stream.guide_for((0, False))
    monitor = display_stream.guide_for((0, False))
    assert panel is not None
    assert monitor == panel


def test_turned_head_gets_no_share_of_the_guide(stream):
    display_stream, _, _ = stream
    assert display_stream.guide_for((0, False)) is not None
    assert display_stream.guide_for((90, False)) is None
    assert display_stream.guide_for((0, True)) is None


def test_reset_lets_a_new_context_claim_the_guide(stream):
    display_stream, _, _ = stream
    first = display_stream.guide_for((0, False))
    display_stream.guide_seq = display_stream.frame_seq
    display_stream.reset()
    assert display_stream.guide_seq != display_stream.frame_seq
    assert display_stream.guide_for((90, False)) not in (None, first)
