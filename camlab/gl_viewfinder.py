# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""GlViewfinder - zero-copy camera viewfinder rendered inside Qt's own scene.

Replaces picamera2's QGlPicamera2, which renders through a private EGL context
into a native Wayland subsurface. That subsurface stacks above every Qt-painted
widget and cannot be overlaid without more subsurfaces, whose late mapping is
broken in qtwayland. Rendering in-scene via QOpenGLWidget keeps the whole UI
in one composited surface, so plain Qt widgets - control sheets, modal cards -
stack above the live picture with translucency.

Zero-copy path is the same trick QGlPicamera2 uses: each camera dmabuf is
imported once as an EGLImage bound to a GL_TEXTURE_EXTERNAL_OES texture
(driver does YUV->RGB), then drawn letterboxed. Textures are cached per
request and dropped when the camera is reconfigured.

set_frosted(True) swaps the passthrough draw for a GPU blur, so modals float
over a live frosted picture. set_assists() switches the sharp draw to a focus
peaking / zebra shader.

samplerExternalOES needs a GLES context: call install_gles_format() before
constructing QApplication.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from typing import ClassVar

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

from OpenGL.EGL.EXT.image_dma_buf_import import (
    EGL_DMA_BUF_PLANE0_FD_EXT,
    EGL_DMA_BUF_PLANE0_OFFSET_EXT,
    EGL_DMA_BUF_PLANE0_PITCH_EXT,
    EGL_DMA_BUF_PLANE1_FD_EXT,
    EGL_DMA_BUF_PLANE1_OFFSET_EXT,
    EGL_DMA_BUF_PLANE1_PITCH_EXT,
    EGL_DMA_BUF_PLANE2_FD_EXT,
    EGL_DMA_BUF_PLANE2_OFFSET_EXT,
    EGL_DMA_BUF_PLANE2_PITCH_EXT,
    EGL_LINUX_DMA_BUF_EXT,
    EGL_LINUX_DRM_FOURCC_EXT,
)
from OpenGL.EGL.KHR.image import eglCreateImageKHR, eglDestroyImageKHR
from OpenGL.EGL.VERSION.EGL_1_0 import (
    EGL_HEIGHT,
    EGL_NO_CONTEXT,
    EGL_NONE,
    EGL_WIDTH,
    eglGetCurrentDisplay,
    eglGetProcAddress,
)
from OpenGL.GL import shaders
from OpenGL.GLES2.OES.EGL_image_external import GL_TEXTURE_EXTERNAL_OES
from OpenGL.GLES2.VERSION.GLES2_2_0 import (
    GL_ARRAY_BUFFER,
    GL_BLEND,
    GL_CLAMP_TO_EDGE,
    GL_COLOR_ATTACHMENT0,
    GL_COLOR_BUFFER_BIT,
    GL_CULL_FACE,
    GL_DEPTH_TEST,
    GL_FALSE,
    GL_FLOAT,
    GL_FRAGMENT_SHADER,
    GL_FRAMEBUFFER,
    GL_FRAMEBUFFER_COMPLETE,
    GL_LINEAR,
    GL_MAX_TEXTURE_SIZE,
    GL_RGBA,
    GL_SCISSOR_TEST,
    GL_TEXTURE0,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S,
    GL_TEXTURE_WRAP_T,
    GL_TRIANGLE_FAN,
    GL_UNSIGNED_BYTE,
    GL_VERTEX_SHADER,
    glActiveTexture,
    glBindBuffer,
    glBindFramebuffer,
    glBindTexture,
    glCheckFramebufferStatus,
    glClear,
    glClearColor,
    glDeleteTextures,
    glDisable,
    glDrawArrays,
    glEnableVertexAttribArray,
    glFramebufferTexture2D,
    glGenFramebuffers,
    glGenTextures,
    glGetAttribLocation,
    glGetIntegerv,
    glGetUniformLocation,
    glTexImage2D,
    glTexParameteri,
    glUniform1f,
    glUniform1i,
    glUniform2f,
    glUseProgram,
    glViewport,
)
from OpenGL.GLES3.VERSION.GLES3_3_0 import glBindVertexArray

# Raw entry point: the PyOpenGL wrapper caches the array in per-context
# storage keyed by eglGetCurrentContext(), which reads 0 inside Qt's
# QOpenGLWidget context and raises. The quad array is kept alive on self.
from OpenGL.raw.GLES2.VERSION.GLES2_2_0 import glVertexAttribPointer
from picamera2.previews.gl_helpers import str_to_fourcc

from .qt import QOpenGLWidget, QtCore, QtGui

log = logging.getLogger(__name__)

# H+V Gaussian iterations at 1/8 scale. Each adds ~sigma 21 px (full-res
# equivalent), two together read as the intended frost strength.
_BLUR_PASSES = 2

_VERT = """
    attribute vec2 aPosition;
    varying vec2 texcoord;

    void main()
    {
        gl_Position = vec4(aPosition * 2.0 - 1.0, 0.0, 1.0);
        texcoord.x = aPosition.x;
        texcoord.y = 1.0 - aPosition.y;
    }
"""

# Identity texcoords for FBO-to-FBO passes (first pass already flipped).
_VERT_PLAIN = """
    attribute vec2 aPosition;
    varying vec2 texcoord;

    void main()
    {
        gl_Position = vec4(aPosition * 2.0 - 1.0, 0.0, 1.0);
        texcoord = aPosition;
    }
"""

_FRAG_EXT = """
    #extension GL_OES_EGL_image_external : enable
    precision mediump float;
    varying vec2 texcoord;
    uniform samplerExternalOES tex;

    void main()
    {
        gl_FragColor = texture2D(tex, texcoord);
    }
"""

_FRAG_2D = """
    precision mediump float;
    varying vec2 texcoord;
    uniform sampler2D tex;

    void main()
    {
        gl_FragColor = texture2D(tex, texcoord);
    }
"""

# Focus peaking + zebra in one pass (after cinepi-kurokesu's overlays).
# Peaking: 2-tap gradient of luma, red where it exceeds the threshold.
# Zebra: animated diagonal black/white stripes where luma clips zebraThr.
# Both gated by 0/1 uniforms, only compiled in when an assist is active.
_FRAG_EXT_FX = """
    #extension GL_OES_EGL_image_external : enable
    precision mediump float;
    varying vec2 texcoord;
    uniform samplerExternalOES tex;
    uniform vec2 texel;
    uniform float peaking;
    uniform float zebra;
    uniform float zebraThr;
    uniform float time;

    const vec3 LUMA = vec3(0.299, 0.587, 0.114);

    void main()
    {
        vec4 color = texture2D(tex, texcoord);
        float centre = dot(color.rgb, LUMA);
        if (zebra > 0.5 && centre > zebraThr) {
            float stripe = mod((texcoord.x + texcoord.y + time * 0.02) / 0.01, 2.0);
            gl_FragColor = stripe < 1.0 ? vec4(0.0, 0.0, 0.0, 1.0)
                                        : vec4(1.0, 1.0, 1.0, 1.0);
            return;
        }
        if (peaking > 0.5) {
            float right = dot(texture2D(tex, texcoord + vec2(texel.x, 0.0)).rgb, LUMA);
            float below = dot(texture2D(tex, texcoord + vec2(0.0, texel.y)).rgb, LUMA);
            if (abs(right - centre) + abs(below - centre) > 0.08) {
                gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);
                return;
            }
        }
        gl_FragColor = color;
    }
"""

# 9-tap separable Gaussian using linear-sampling offsets (5 fetches).
# texel is one texel along the blur axis, zero on the other.
_FRAG_BLUR = """
    precision mediump float;
    varying vec2 texcoord;
    uniform sampler2D tex;
    uniform vec2 texel;

    void main()
    {
        vec4 c = texture2D(tex, texcoord) * 0.227027;
        c += (texture2D(tex, texcoord + texel * 1.384615)
            + texture2D(tex, texcoord - texel * 1.384615)) * 0.316216;
        c += (texture2D(tex, texcoord + texel * 3.230769)
            + texture2D(tex, texcoord - texel * 3.230769)) * 0.070270;
        gl_FragColor = c;
    }
"""


def install_gles_format() -> None:
    """Make GLES the app-wide default context type (call before QApplication).

    Mesa V3D offers desktop GL 3.1 without GL_OES_EGL_image_external, so the
    dmabuf external-image path needs a GLES context.
    """
    fmt = QtGui.QSurfaceFormat()
    fmt.setRenderableType(QtGui.QSurfaceFormat.RenderableType.OpenGLES)
    fmt.setVersion(2, 0)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)


def _compile(src: str, kind):
    sh = shaders.compileShader(src, kind)
    # compileShader occasionally returns a 1-tuple (see upstream q_gl_picamera2).
    return sh[0] if isinstance(sh, tuple) else sh


# glEGLImageTargetTexture2DOES resolved by hand: PyOpenGL's lazy loader
# refuses it inside Qt's context (its cached GL extension probe predates the
# context), so ask EGL for the pointer directly.
_egl_image_target_fn = None


def _egl_image_target_texture(target, image) -> None:
    global _egl_image_target_fn
    if _egl_image_target_fn is None:
        ptr = eglGetProcAddress(b"glEGLImageTargetTexture2DOES")
        addr = ctypes.cast(ptr, ctypes.c_void_p).value if ptr else None
        if not addr:
            raise RuntimeError("glEGLImageTargetTexture2DOES unavailable")
        _egl_image_target_fn = ctypes.CFUNCTYPE(
            None, ctypes.c_uint32, ctypes.c_void_p)(addr)
    _egl_image_target_fn(int(target), ctypes.cast(image, ctypes.c_void_p))


class _Buffer:
    """One camera dmabuf imported as an external GL texture (zero-copy)."""

    # libcamera format string -> DRM fourcc (24-bit formats unsupported).
    FMT_MAP: ClassVar[dict[str, str]] = {
        "XRGB8888": "XR24",
        "XBGR8888": "XB24",
        "YUYV": "YUYV",
        "UYVY": "UYVY",
        "YUV420": "YU12",
        "YVU420": "YV12",
    }

    def __init__(self, display, completed_request, max_texture_size):
        picam2 = completed_request.picam2
        stream = picam2.stream_map[picam2.display_stream_name]
        fb = completed_request.request.buffers[stream]

        cfg = stream.configuration
        pixel_format = str(cfg.pixel_format)
        if pixel_format not in self.FMT_MAP:
            raise RuntimeError(f"format {pixel_format} not supported by GlViewfinder")
        fmt = str_to_fourcc(self.FMT_MAP[pixel_format])
        w, h = cfg.size.width, cfg.size.height
        if w > max_texture_size or h > max_texture_size:
            raise RuntimeError(f"maximum supported viewfinder size is {max_texture_size}")
        if pixel_format in ("YUV420", "YVU420"):
            h2 = h // 2
            stride2 = cfg.stride // 2
            attribs = [
                EGL_WIDTH, w,
                EGL_HEIGHT, h,
                EGL_LINUX_DRM_FOURCC_EXT, fmt,
                EGL_DMA_BUF_PLANE0_FD_EXT, fb.planes[0].fd,
                EGL_DMA_BUF_PLANE0_OFFSET_EXT, 0,
                EGL_DMA_BUF_PLANE0_PITCH_EXT, cfg.stride,
                EGL_DMA_BUF_PLANE1_FD_EXT, fb.planes[0].fd,
                EGL_DMA_BUF_PLANE1_OFFSET_EXT, h * cfg.stride,
                EGL_DMA_BUF_PLANE1_PITCH_EXT, stride2,
                EGL_DMA_BUF_PLANE2_FD_EXT, fb.planes[0].fd,
                EGL_DMA_BUF_PLANE2_OFFSET_EXT, h * cfg.stride + h2 * stride2,
                EGL_DMA_BUF_PLANE2_PITCH_EXT, stride2,
                EGL_NONE,
            ]
        else:
            attribs = [
                EGL_WIDTH, w,
                EGL_HEIGHT, h,
                EGL_LINUX_DRM_FOURCC_EXT, fmt,
                EGL_DMA_BUF_PLANE0_FD_EXT, fb.planes[0].fd,
                EGL_DMA_BUF_PLANE0_OFFSET_EXT, 0,
                EGL_DMA_BUF_PLANE0_PITCH_EXT, cfg.stride,
                EGL_NONE,
            ]

        image = eglCreateImageKHR(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT,
                                  None, attribs)
        self.texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, self.texture)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        _egl_image_target_texture(GL_TEXTURE_EXTERNAL_OES, image)
        eglDestroyImageKHR(display, image)


class GlViewfinder(QOpenGLWidget):
    """In-scene zero-copy viewfinder widget driving the picamera2 event loop."""

    def __init__(self, picam2, parent=None):
        super().__init__(parent)
        self.picamera2 = picam2
        # Pure black pillarboxes: the picture reads as the natural focus
        # target against them, and they blend into a dark bench.
        self._bg = (0.0, 0.0, 0.0, 1.0)
        self.current_request = None
        self.own_current = False
        self._buffers: dict = {}          # libcamera request -> _Buffer
        self._stop_count = 0
        self._frosted = False
        self._frost_broken = False
        self._import_err_logged = False
        self._peaking = False
        self._zebra = False
        self._zebra_thr = 0.95
        self._fx_t0 = time.monotonic()  # zebra stripe animation epoch
        # ctypes array (not a list): raw glVertexAttribPointer takes the
        # pointer as-is, and GL reads it at every draw, so it must stay alive.
        self._quad = (ctypes.c_float * 8)(0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
        self._target_size: tuple[int, int] | None = None

        picam2.attach_preview(None)
        self._notifier = QtCore.QSocketNotifier(
            picam2.notifyme_r, QtCore.QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._handle_requests)
        self.running = True
        self.destroyed.connect(lambda: self._teardown())

    # picamera2 event-loop contract
    def _handle_requests(self) -> None:
        if not self.running:
            return
        self.picamera2.notifymeread.read()
        self.picamera2.process_requests(self)

    def render_request(self, completed_request) -> None:
        """Called by picamera2 with each frame to display (GUI thread).

        Pull model: hold only the newest request (the previous one goes back
        to the pipeline) and make sure one repaint is scheduled. The paint
        draws whatever request is newest when it runs, so a stream faster
        than the display collapses to latest-frame-wins and the camera rate
        is never throttled by the screen.
        """
        if self.current_request is not None and self.own_current:
            self.current_request.release()
        self.current_request = completed_request
        self.own_current = completed_request.config['buffer_count'] > 1
        if self.own_current:
            self.current_request.acquire()
        # update() coalesces (Qt paints once per compositor frame callback),
        # so no explicit pacing is needed here.
        self.update()

    def _teardown(self) -> None:
        if not self.running:
            return
        self.running = False
        self._notifier.setEnabled(False)
        if self.current_request is not None and self.own_current:
            self.current_request.release()
        self.current_request = None
        self.picamera2.detach_preview()

    def closeEvent(self, event) -> None:
        self._teardown()
        super().closeEvent(event)

    # frost
    def set_frosted(self, frosted: bool) -> None:
        """Blur (True) or passthrough (False) rendering of the live stream."""
        frosted = bool(frosted) and not self._frost_broken
        if frosted != self._frosted:
            self._frosted = frosted
            self.update()

    # display assists
    def set_assists(self, peaking: bool, zebra: bool,
                    zebra_threshold: float) -> None:
        """Toggle focus peaking / zebra and set the zebra clip level (0..1)."""
        self._peaking = bool(peaking)
        self._zebra = bool(zebra)
        self._zebra_thr = min(max(float(zebra_threshold), 0.0), 1.0)
        self.update()

    # GL
    def initializeGL(self) -> None:
        self._egl_display = eglGetCurrentDisplay()
        self._max_texture_size = int(glGetIntegerv(GL_MAX_TEXTURE_SIZE))
        self._attr_locs: dict = {}
        self._prog_ext = self._build_program(_VERT, _FRAG_EXT)
        self._prog_copy = self._build_program(_VERT_PLAIN, _FRAG_2D)
        self._prog_blur = self._build_program(_VERT_PLAIN, _FRAG_BLUR)
        self._blur_step = glGetUniformLocation(self._prog_blur, "texel")
        self._prog_fx = None  # compiled on first assist use
        self._fbos = [int(f) for f in glGenFramebuffers(3)]
        self._texs = [int(t) for t in glGenTextures(3)]
        # Context loss (e.g. reparenting a realized widget) invalidates every
        # cached texture id along with the context they lived in.
        self._buffers = {}
        self._target_size = None

    def _build_program(self, vsrc: str, fsrc: str):
        prog = shaders.compileProgram(_compile(vsrc, GL_VERTEX_SHADER),
                                      _compile(fsrc, GL_FRAGMENT_SHADER))
        self._attr_locs[prog] = glGetAttribLocation(prog, "aPosition")
        glUseProgram(prog)
        glUniform1i(glGetUniformLocation(prog, "tex"), 0)
        return prog

    def _use(self, prog) -> None:
        """Activate a program with its aPosition attribute fed from the quad.

        Rebinding the pointer per use, not once at init: Qt drives its own
        VAOs through this shared context and client attribute state lives in
        whatever VAO is bound, so anything set earlier is long gone.
        """
        glUseProgram(prog)
        loc = self._attr_locs[prog]
        glVertexAttribPointer(loc, 2, GL_FLOAT, GL_FALSE, 0, self._quad)
        glEnableVertexAttribArray(loc)

    @staticmethod
    def _reset_gl_state() -> None:
        """Return Qt's context to GLES defaults our passes rely on."""
        glBindVertexArray(0)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glActiveTexture(GL_TEXTURE0)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_SCISSOR_TEST)
        glDisable(GL_CULL_FACE)
        glDisable(GL_BLEND)

    def paintGL(self) -> None:
        self._reset_gl_state()
        glClearColor(*self._bg)
        glClear(GL_COLOR_BUFFER_BIT)
        req = self.current_request
        if req is None:
            return
        try:
            texture = self._texture_for(req)
        except Exception:
            # Log once, not per frame (34 Hz would flood the journal).
            if not self._import_err_logged:
                self._import_err_logged = True
                log.exception("dmabuf import failed")
            return
        vx, vy, vw, vh = self._letterbox_viewport()
        if self._frosted:
            try:
                self._draw_frosted(texture, (vx, vy, vw, vh))
                return
            except Exception:
                # Broken frost must never kill the viewfinder: back to sharp for good.
                log.exception("frost render failed, disabling")
                self._frost_broken = True
                self._frosted = False
                glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        glViewport(vx, vy, vw, vh)
        if self._peaking or self._zebra:
            self._use_fx()
        else:
            self._use(self._prog_ext)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, texture)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

    def _use_fx(self) -> None:
        """Activate the assist (peaking/zebra) program with fresh uniforms."""
        if self._prog_fx is None:
            self._prog_fx = self._build_program(_VERT, _FRAG_EXT_FX)
            self._fx_locs = {name: glGetUniformLocation(self._prog_fx, name)
                             for name in ("texel", "peaking", "zebra",
                                          "zebraThr", "time")}
        self._use(self._prog_fx)
        try:
            iw, ih = self._display_size()
        except Exception:  # noqa: BLE001 fall back to widget size
            iw, ih = self.width(), self.height()
        loc = self._fx_locs
        glUniform2f(loc["texel"], 1.0 / max(iw, 1), 1.0 / max(ih, 1))
        glUniform1f(loc["peaking"], 1.0 if self._peaking else 0.0)
        glUniform1f(loc["zebra"], 1.0 if self._zebra else 0.0)
        glUniform1f(loc["zebraThr"], self._zebra_thr)
        # Wrapped epoch keeps mediump float precise (stripes drift, never jump).
        glUniform1f(loc["time"], (time.monotonic() - self._fx_t0) % 3600.0)

    def _texture_for(self, completed_request) -> int:
        if completed_request.request not in self._buffers:
            if self._stop_count != self.picamera2.stop_count:
                # Reconfigured: every cached request is stale, textures included.
                for buffer in self._buffers.values():
                    glDeleteTextures(1, [buffer.texture])
                self._buffers = {}
                self._stop_count = self.picamera2.stop_count
            self._buffers[completed_request.request] = _Buffer(
                self._egl_display, completed_request, self._max_texture_size)
        return self._buffers[completed_request.request].texture

    def _display_size(self) -> tuple[int, int]:
        cfg = self.picamera2.stream_map[
            self.picamera2.camera_config['display']].configuration
        return cfg.size.width, cfg.size.height

    def _letterbox_viewport(self) -> tuple[int, int, int, int]:
        dpr = self.devicePixelRatioF()
        ww, wh = round(self.width() * dpr), round(self.height() * dpr)
        try:
            iw, ih = self._display_size()
        except Exception:  # noqa: BLE001 no stream size yet, fill the widget
            return 0, 0, ww, wh
        if iw * wh > ww * ih:
            w = ww
            h = w * ih // iw
        else:
            h = wh
            w = h * iw // ih
        return (ww - w) // 2, (wh - h) // 2, w, h

    # frost chain (camera -> 1/4 -> 1/8 -> Gaussian ping-pong -> screen)
    def _draw_frosted(self, camera_texture: int, viewport) -> None:
        iw, ih = self._display_size()
        self._ensure_targets(iw, ih)
        (aw, ah), (bw, bh) = self._sizes[0], self._sizes[1]
        a_fbo, b_fbo, c_fbo = self._fbos
        a_tex, b_tex, c_tex = self._texs

        # camera (external) -> A at 1/4 (flip happens here, in _VERT)
        glBindFramebuffer(GL_FRAMEBUFFER, a_fbo)
        glViewport(0, 0, aw, ah)
        self._use(self._prog_ext)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, camera_texture)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
        # A -> B at 1/8
        glBindFramebuffer(GL_FRAMEBUFFER, b_fbo)
        glViewport(0, 0, bw, bh)
        self._use(self._prog_copy)
        glBindTexture(GL_TEXTURE_2D, a_tex)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
        # Gaussian ping-pong B <-> C, result lands back in B
        self._use(self._prog_blur)
        for _ in range(_BLUR_PASSES):
            glBindFramebuffer(GL_FRAMEBUFFER, c_fbo)
            glBindTexture(GL_TEXTURE_2D, b_tex)
            glUniform2f(self._blur_step, 1.0 / bw, 0.0)
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
            glBindFramebuffer(GL_FRAMEBUFFER, b_fbo)
            glBindTexture(GL_TEXTURE_2D, c_tex)
            glUniform2f(self._blur_step, 0.0, 1.0 / bh)
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
        # B -> screen. _VERT_PLAIN keeps orientation, pass 1 already flipped.
        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        glViewport(*viewport)
        self._use(self._prog_copy)
        glBindTexture(GL_TEXTURE_2D, b_tex)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

    def _ensure_targets(self, width: int, height: int) -> None:
        """(Re)allocate blur textures when the display stream size changes."""
        if self._target_size == (width, height):
            return
        self._sizes = [
            (max(1, width // 4), max(1, height // 4)),
            (max(1, width // 8), max(1, height // 8)),
            (max(1, width // 8), max(1, height // 8)),
        ]
        for fbo, tex, (tw, th) in zip(self._fbos, self._texs, self._sizes):
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, tw, th, 0,
                         GL_RGBA, GL_UNSIGNED_BYTE, None)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, tex, 0)
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("frost framebuffer incomplete")
        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        self._target_size = (width, height)
