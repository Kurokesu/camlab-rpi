# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""GlViewfinder: zero-copy camera viewfinder rendered inside Qt scene.

Replaces QGlPicamera2 subsurface path. In-scene QOpenGLWidget keeps UI in one
surface, plain widgets stack above with translucency. Each dmabuf imported as
EGLImage to GL_TEXTURE_EXTERNAL_OES, letterboxed draw. set_frosted swaps blur for
modals. set_assists adds peaking/zebra. Call install_gles_format() before QApplication.
"""

from __future__ import annotations

import ctypes
import logging
import math
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
from OpenGL.error import Error as OpenGLError
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
    GL_VALIDATE_STATUS,
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
    glGetProgramInfoLog,
    glGetProgramiv,
    glGetUniformLocation,
    glTexImage2D,
    glTexParameteri,
    glUniform1f,
    glUniform1i,
    glUniform2f,
    glUniform3f,
    glUniformMatrix2fv,
    glUseProgram,
    glValidateProgram,
    glViewport,
)

# GL_EXT_texture_rg shares these enums, so ES2 carrying it works too.
from OpenGL.GLES3.VERSION.GLES3_3_0 import GL_R8, GL_RED, GL_RG, GL_RG8, glBindVertexArray

# Raw entry point: PyOpenGL wrapper caches array per-context keyed by
# eglGetCurrentContext(), reads 0 inside QOpenGLWidget and raises.
from OpenGL.raw.GLES2.VERSION.GLES2_2_0 import glVertexAttribPointer
from picamera2.previews.gl_helpers import str_to_fourcc

from .qt import QOpenGLWidget, QtCore, QtGui

log = logging.getLogger(__name__)

# H+V Gaussian iterations at 1/8 scale. Each adds ~sigma 21 px (full-res
# equivalent), two together read as the intended frost strength.
_BLUR_PASSES = 2

# uRotate turns sampled texcoord about center, portrait panel shows landscape sensor upright.
# Identity at 0 degrees.
_VERT = """
    attribute vec2 aPosition;
    varying vec2 texcoord;
    uniform mat2 uRotate;

    void main()
    {
        gl_Position = vec4(aPosition * 2.0 - 1.0, 0.0, 1.0);
        vec2 tc = vec2(aPosition.x, 1.0 - aPosition.y);
        texcoord = uRotate * (tc - 0.5) + 0.5;
    }
"""

# Guide is already in displayed orientation, so it comes straight off the quad.
_VERT_FX = """
    attribute vec2 aPosition;
    varying vec2 texcoord;
    varying vec2 guidecoord;
    uniform mat2 uRotate;

    void main()
    {
        gl_Position = vec4(aPosition * 2.0 - 1.0, 0.0, 1.0);
        vec2 tc = vec2(aPosition.x, 1.0 - aPosition.y);
        texcoord = uRotate * (tc - 0.5) + 0.5;
        guidecoord = aPosition;
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

# Peaking guide: mean and gradient sector, smooth enough for half resolution.
# Paint does the sharp work against them.
_FRAG_GUIDE = """
    precision mediump float;
    varying vec2 texcoord;
    uniform sampler2D tex;
    uniform vec2 stepX;
    uniform vec2 stepY;
    uniform float gain;

    float luma(vec2 at)
    {
        return texture2D(tex, at).r * gain;
    }

    void main()
    {
        float c = luma(texcoord);
        float xp = luma(texcoord + stepX);
        float xm = luma(texcoord - stepX);
        float yp = luma(texcoord + stepY);
        float ym = luma(texcoord - stepY);
        float gx = xp - xm;
        float gy = yp - ym;
        // Gradient quantized to 4 sectors, 0.414 is tan(22.5 deg)
        float sector = 0.25;
        if (abs(gy) < 0.414 * abs(gx)) sector = 0.0;
        else if (abs(gx) < 0.414 * abs(gy)) sector = 0.5;
        else if (gx * gy < 0.0) sector = 0.75;
        gl_FragColor = vec4((c + xp + xm + yp + ym) * 0.2, sector, 0.0, 1.0);
    }
"""

# Luma for formats with no plane to import. Plain texcoords keep the target in
# camera orientation, like an imported plane.
_FRAG_LUMA = """
    #extension GL_OES_EGL_image_external : enable
    precision mediump float;
    varying vec2 texcoord;
    uniform samplerExternalOES tex;

    const vec3 WEIGHTS = vec3(0.299, 0.587, 0.114);

    void main()
    {
        gl_FragColor = vec4(dot(texture2D(tex, texcoord).rgb, WEIGHTS), 0.0, 0.0, 1.0);
    }
"""

# Paint pass: marks and zebra over the frame, gated by 0/1 uniforms and compiled
# on first assist use.
# Zebra: animated diagonal black/white stripes where luma clips zebraThr.
_FRAG_EXT_FX = """
    #extension GL_OES_EGL_image_external : enable
    precision mediump float;
    varying vec2 texcoord;
    varying vec2 guidecoord;
    uniform samplerExternalOES tex;
    uniform sampler2D guide;
    uniform sampler2D luma;
    uniform vec2 stepX;
    uniform vec2 stepY;
    uniform vec2 guideX;
    uniform vec2 guideY;
    uniform vec3 peakColor;
    uniform float peakThr;
    uniform float peaking;
    uniform float zebra;
    uniform float zebraThr;
    uniform float time;
    uniform float gain;

    const vec3 WEIGHTS = vec3(0.299, 0.587, 0.114);

    float lum(vec2 at)
    {
        return texture2D(luma, at).r * gain;
    }

    void main()
    {
        vec4 color = texture2D(tex, texcoord);
        if (zebra > 0.5 && dot(color.rgb, WEIGHTS) > zebraThr) {
            float stripe = mod((texcoord.x + texcoord.y + time * 0.02) / 0.01, 2.0);
            gl_FragColor = stripe < 1.0 ? vec4(0.0, 0.0, 0.0, 1.0)
                                        : vec4(1.0, 1.0, 1.0, 1.0);
            return;
        }
        if (peaking > 0.5) {
            vec2 g = texture2D(guide, guidecoord).rg;
            float sector = floor(g.g * 4.0 + 0.5);
            vec2 d = stepX + stepY;
            vec2 dg = guideX + guideY;
            if (sector < 0.5) { d = stepX; dg = guideX; }
            else if (sector > 1.5 && sector < 2.5) { d = stepY; dg = guideY; }
            else if (sector > 2.5) { d = stepX - stepY; dg = guideX - guideY; }
            // Pixel against its own neighborhood mean, across the edge
            float c = lum(texcoord) - g.r;
            float f = lum(texcoord + d) - texture2D(guide, guidecoord + dg).r;
            float b = lum(texcoord - d) - texture2D(guide, guidecoord - dg).r;
            // Sign flips on the edge itself, so a mark is one pixel and lands
            // where the edge is. Amplitude stays low on blur, shade and noise.
            bool cross = (c * f < 0.0 && abs(c) <= abs(f)) || (c * b < 0.0 && abs(c) < abs(b));
            if (cross && abs(f - b) > peakThr) {
                gl_FragColor = vec4(peakColor, 1.0);
                return;
            }
        }
        gl_FragColor = color;
    }
"""

_PEAK_COLOR = (1.0, 0.0, 0.0)
_PEAK_THR = 0.12
# Plane luma is studio range, stretch it to match a conversion.
_LUMA_GAIN = 255.0 / 219.0


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
        _egl_image_target_fn = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_void_p)(addr)
    _egl_image_target_fn(int(target), ctypes.cast(image, ctypes.c_void_p))


def _import_luma(display, fd: int, width: int, height: int, stride: int) -> int:
    """Import plane 0 of a planar YUV dmabuf as an R8 2D texture."""
    attribs = [
        EGL_WIDTH,
        width,
        EGL_HEIGHT,
        height,
        EGL_LINUX_DRM_FOURCC_EXT,
        str_to_fourcc("R8  "),
        EGL_DMA_BUF_PLANE0_FD_EXT,
        fd,
        EGL_DMA_BUF_PLANE0_OFFSET_EXT,
        0,
        EGL_DMA_BUF_PLANE0_PITCH_EXT,
        stride,
        EGL_NONE,
    ]
    image = eglCreateImageKHR(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, None, attribs)
    if not image:
        raise RuntimeError("luma plane import rejected")
    texture = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, texture)
    # Bilinear, both passes sample off their own grid
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    _egl_image_target_texture(GL_TEXTURE_2D, image)
    eglDestroyImageKHR(display, image)
    return texture


class _Buffer:
    """One camera dmabuf imported as an external GL texture (zero-copy)."""

    luma_warned = False

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
                EGL_WIDTH,
                w,
                EGL_HEIGHT,
                h,
                EGL_LINUX_DRM_FOURCC_EXT,
                fmt,
                EGL_DMA_BUF_PLANE0_FD_EXT,
                fb.planes[0].fd,
                EGL_DMA_BUF_PLANE0_OFFSET_EXT,
                0,
                EGL_DMA_BUF_PLANE0_PITCH_EXT,
                cfg.stride,
                EGL_DMA_BUF_PLANE1_FD_EXT,
                fb.planes[0].fd,
                EGL_DMA_BUF_PLANE1_OFFSET_EXT,
                h * cfg.stride,
                EGL_DMA_BUF_PLANE1_PITCH_EXT,
                stride2,
                EGL_DMA_BUF_PLANE2_FD_EXT,
                fb.planes[0].fd,
                EGL_DMA_BUF_PLANE2_OFFSET_EXT,
                h * cfg.stride + h2 * stride2,
                EGL_DMA_BUF_PLANE2_PITCH_EXT,
                stride2,
                EGL_NONE,
            ]
        else:
            attribs = [
                EGL_WIDTH,
                w,
                EGL_HEIGHT,
                h,
                EGL_LINUX_DRM_FOURCC_EXT,
                fmt,
                EGL_DMA_BUF_PLANE0_FD_EXT,
                fb.planes[0].fd,
                EGL_DMA_BUF_PLANE0_OFFSET_EXT,
                0,
                EGL_DMA_BUF_PLANE0_PITCH_EXT,
                cfg.stride,
                EGL_NONE,
            ]

        image = eglCreateImageKHR(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, None, attribs)
        self.texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, self.texture)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        _egl_image_target_texture(GL_TEXTURE_EXTERNAL_OES, image)
        eglDestroyImageKHR(display, image)
        # Peaking wants luma only and plane 0 holds it. R8 costs a plain fetch.
        self.luma = None
        if pixel_format in ("YUV420", "YVU420"):
            try:
                self.luma = _import_luma(display, fb.planes[0].fd, w, h, cfg.stride)
            except (OpenGLError, RuntimeError):
                if not _Buffer.luma_warned:
                    _Buffer.luma_warned = True
                    log.warning("luma plane import failed, peaking converts instead")


class GlViewfinder(QOpenGLWidget):
    """In-scene zero-copy viewfinder widget driving the picamera2 event loop."""

    def __init__(self, picam2, parent=None, transform: int = 0, mirror: bool = False):
        super().__init__(parent)
        self.picamera2 = picam2
        if transform not in (0, 90, 180, 270):
            raise ValueError(f"transform must be 0, 90, 180 or 270 (got {transform})")
        self._transform = transform
        # Preview only, captures are untouched. A booth wants a mirror to frame
        # against, not flipped stills.
        self._mirror = bool(mirror)
        # Pure black pillarboxes: picture reads as natural focus target, blend into dark bench.
        self._bg = (0.0, 0.0, 0.0, 1.0)
        self.current_request = None
        self.own_current = False
        self._buffers: dict = {}  # libcamera request -> _Buffer
        self._stop_count = 0
        self._frosted = False
        self._frost_broken = False
        self._import_err_logged = False
        self._peaking = False
        self._zebra = False
        self._zebra_thr = 0.95
        self._fx_t0 = time.monotonic()  # zebra stripe animation epoch
        # ctypes array not list: raw glVertexAttribPointer takes pointer as-is,
        # GL reads at every draw, must stay alive.
        self._quad = (ctypes.c_float * 8)(0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)
        self._target_size: tuple[int, int] | None = None

        picam2.attach_preview(None)
        self._notifier = QtCore.QSocketNotifier(
            picam2.notifyme_r, QtCore.QSocketNotifier.Type.Read, self
        )
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
        self.own_current = completed_request.config["buffer_count"] > 1
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
    def set_assists(self, peaking: bool, zebra: bool, zebra_threshold: float) -> None:
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
        # 0..2 frost chain, 3 peaking guide, 4 luma when no plane to import
        self._fbos = [int(f) for f in glGenFramebuffers(5)]
        self._texs = [int(t) for t in glGenTextures(5)]
        self._assist_sizes: dict[int, tuple[int, int]] = {}
        # Context loss (e.g. reparenting a realized widget) invalidates every
        # cached texture id along with the context they lived in.
        self._buffers = {}
        self._target_size = None

    def _build_program(self, vsrc: str, fsrc: str, samplers: dict[str, int] | None = None):
        # Samplers default to unit 0 at link time, invalid for two sampler types,
        # so validate after assigning.
        prog = shaders.compileProgram(
            _compile(vsrc, GL_VERTEX_SHADER), _compile(fsrc, GL_FRAGMENT_SHADER), validate=False
        )
        self._attr_locs[prog] = glGetAttribLocation(prog, "aPosition")
        glUseProgram(prog)
        for name, unit in (samplers or {"tex": 0}).items():
            glUniform1i(glGetUniformLocation(prog, name), unit)
        # uRotate exists only in _VERT programs. Seed it so a program left at
        # its default (0 matrix) never samples a collapsed texcoord.
        loc = glGetUniformLocation(prog, "uRotate")
        if loc != -1:
            glUniformMatrix2fv(loc, 1, GL_FALSE, self._rotate_matrix())
        glValidateProgram(prog)
        if not glGetProgramiv(prog, GL_VALIDATE_STATUS):
            log.warning("program validation failed: %s", glGetProgramInfoLog(prog))
        return prog

    def _rotate_matrix(self):
        """Column-major mat2 turns centered texcoord by -transform, picture turns by +transform.

        Mirror negates the first column, reflecting the picture left to right as
        shown. Folded in here rather than into the turn so the axis is the
        screen's at any transform, and the determinant goes negative.
        """
        angle = math.radians(-self._transform)
        c, s = math.cos(angle), math.sin(angle)
        flip = -1.0 if self._mirror else 1.0
        # glUniformMatrix2fv with transpose=FALSE reads column-major: [m00, m10, m01, m11].
        return (ctypes.c_float * 4)(c * flip, s * flip, -s, c)

    def _displayed(self, iw: int, ih: int) -> tuple[int, int]:
        """Size as shown: a quarter turn presents stored height as width."""
        return (ih, iw) if self._transform in (90, 270) else (iw, ih)

    def _use(self, prog) -> None:
        """Activate program with aPosition fed from quad.

        Rebind pointer per use, not once at init: Qt drives VAOs through shared
        context and client attribute state lives in bound VAO.
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
            buffer = self._buffer_for(req)
        except Exception:
            # Log once, not per frame (34 Hz would flood the journal).
            if not self._import_err_logged:
                self._import_err_logged = True
                log.exception("dmabuf import failed")
            return
        texture = buffer.texture
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
        if self._peaking or self._zebra:
            try:
                self._draw_fx(buffer, (vx, vy, vw, vh))
                return
            except Exception:
                # Assists are optional: never let one take the viewfinder down.
                log.exception("assist render failed, disabling")
                self._peaking = self._zebra = False
                glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        glViewport(vx, vy, vw, vh)
        self._use(self._prog_ext)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, texture)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

    # assist chain (luma -> guide -> marks over the frame)
    def _draw_fx(self, buffer, viewport) -> None:
        vx, vy, vw, vh = viewport
        self._ensure_fx_programs()
        luma, gain = buffer.luma, _LUMA_GAIN
        if luma is None:
            luma, gain = self._render_luma(buffer.texture, vw, vh), 1.0
        glActiveTexture(GL_TEXTURE0 + 2)
        glBindTexture(GL_TEXTURE_2D, luma)
        glActiveTexture(GL_TEXTURE0)
        if self._peaking:
            gw, gh = max(1, vw // 2), max(1, vh // 2)
            self._ensure_target(3, gw, gh, GL_RG8, GL_RG, GL_LINEAR)
            glBindFramebuffer(GL_FRAMEBUFFER, self._fbos[3])
            glViewport(0, 0, gw, gh)
            self._use(self._prog_guide)
            sx, sy = self._peak_basis(gw, gh)
            glUniform2f(self._guide_locs["stepX"], *sx)
            glUniform2f(self._guide_locs["stepY"], *sy)
            glUniform1f(self._guide_locs["gain"], gain)
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
            glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        glViewport(vx, vy, vw, vh)
        self._use_fx(vw, vh, gain)
        glActiveTexture(GL_TEXTURE0 + 1)
        glBindTexture(GL_TEXTURE_2D, self._texs[3])
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, buffer.texture)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

    def _render_luma(self, camera_texture: int, width: int, height: int) -> int:
        """Convert camera texture to an R8 target, for formats with no plane."""
        self._ensure_target(4, width, height, GL_R8, GL_RED, GL_LINEAR)
        glBindFramebuffer(GL_FRAMEBUFFER, self._fbos[4])
        glViewport(0, 0, width, height)
        self._use(self._prog_luma)
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, camera_texture)
        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        return self._texs[4]

    def _peak_basis(self, gw: int, gh: int) -> tuple[tuple[float, float], tuple[float, float]]:
        """One grid pixel in camera texcoord space, so a turned panel thins across
        the edge it shows, not the sensor's axis."""
        m = self._rotate_matrix()  # column-major [m00, m10, m01, m11]
        dx, dy = 1.0 / max(gw, 1), 1.0 / max(gh, 1)
        return (m[0] * dx, m[1] * dx), (m[2] * dy, m[3] * dy)

    def _ensure_fx_programs(self) -> None:
        if self._prog_fx is not None:
            return
        self._prog_guide = self._build_program(_VERT, _FRAG_GUIDE, {"tex": 2})
        names = ("stepX", "stepY", "gain")
        self._guide_locs = {n: glGetUniformLocation(self._prog_guide, n) for n in names}
        self._prog_luma = self._build_program(_VERT_PLAIN, _FRAG_LUMA)
        self._prog_fx = self._build_program(
            _VERT_FX, _FRAG_EXT_FX, {"tex": 0, "guide": 1, "luma": 2}
        )
        names = (
            "stepX",
            "stepY",
            "guideX",
            "guideY",
            "peaking",
            "zebra",
            "zebraThr",
            "time",
            "gain",
        )
        self._fx_locs = {name: glGetUniformLocation(self._prog_fx, name) for name in names}
        # _build_program leaves the program current. Color and threshold never
        # change, upload once.
        glUniform3f(glGetUniformLocation(self._prog_fx, "peakColor"), *_PEAK_COLOR)
        glUniform1f(glGetUniformLocation(self._prog_fx, "peakThr"), _PEAK_THR)

    def _ensure_target(self, slot: int, width: int, height: int, internal, fmt, filt) -> None:
        """(Re)allocate an assist target when the letterboxed viewport changes."""
        if self._assist_sizes.get(slot) == (width, height):
            return
        glBindTexture(GL_TEXTURE_2D, self._texs[slot])
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filt)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filt)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glTexImage2D(GL_TEXTURE_2D, 0, internal, width, height, 0, fmt, GL_UNSIGNED_BYTE, None)
        glBindFramebuffer(GL_FRAMEBUFFER, self._fbos[slot])
        glFramebufferTexture2D(
            GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self._texs[slot], 0
        )
        if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("assist framebuffer incomplete")
        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        self._assist_sizes[slot] = (width, height)

    def _use_fx(self, vw: int, vh: int, gain: float) -> None:
        """Activate the assist (peaking/zebra) program with per-frame uniforms."""
        self._use(self._prog_fx)
        loc = self._fx_locs
        sx, sy = self._peak_basis(vw, vh)
        glUniform2f(loc["stepX"], *sx)
        glUniform2f(loc["stepY"], *sy)
        # Guide is display oriented, so the same step unrotated, y flipped
        glUniform2f(loc["guideX"], 1.0 / max(vw, 1), 0.0)
        glUniform2f(loc["guideY"], 0.0, -1.0 / max(vh, 1))
        glUniform1f(loc["gain"], gain)
        glUniform1f(loc["peaking"], 1.0 if self._peaking else 0.0)
        glUniform1f(loc["zebra"], 1.0 if self._zebra else 0.0)
        glUniform1f(loc["zebraThr"], self._zebra_thr)
        # Wrapped epoch keeps mediump float precise (stripes drift, never jump).
        glUniform1f(loc["time"], (time.monotonic() - self._fx_t0) % 3600.0)

    def _buffer_for(self, completed_request) -> _Buffer:
        if completed_request.request not in self._buffers:
            if self._stop_count != self.picamera2.stop_count:
                # Reconfigured: every cached request is stale, textures included.
                for buffer in self._buffers.values():
                    glDeleteTextures(1, [buffer.texture])
                    if buffer.luma is not None:
                        glDeleteTextures(1, [buffer.luma])
                self._buffers = {}
                self._stop_count = self.picamera2.stop_count
            self._buffers[completed_request.request] = _Buffer(
                self._egl_display, completed_request, self._max_texture_size
            )
        return self._buffers[completed_request.request]

    def _display_size(self) -> tuple[int, int]:
        cfg = self.picamera2.stream_map[self.picamera2.camera_config["display"]].configuration
        return cfg.size.width, cfg.size.height

    def _letterbox_viewport(self) -> tuple[int, int, int, int]:
        dpr = self.devicePixelRatioF()
        ww, wh = round(self.width() * dpr), round(self.height() * dpr)
        try:
            iw, ih = self._display_size()
        except Exception:  # noqa: BLE001 no stream size yet, fill the widget
            return 0, 0, ww, wh
        iw, ih = self._displayed(iw, ih)
        if iw * wh > ww * ih:
            w = ww
            h = w * ih // iw
        else:
            h = wh
            w = h * iw // ih
        return (ww - w) // 2, (wh - h) // 2, w, h

    # frost chain (camera -> 1/4 -> 1/8 -> Gaussian ping-pong -> screen)
    def _draw_frosted(self, camera_texture: int, viewport) -> None:
        # camera -> A rotates while sampling, so A onward is already displayed
        # orientation. FBOs must match it or the frost squashes.
        iw, ih = self._displayed(*self._display_size())
        self._ensure_targets(iw, ih)
        (aw, ah), (bw, bh) = self._sizes[0], self._sizes[1]
        a_fbo, b_fbo, c_fbo = self._fbos[:3]
        a_tex, b_tex, c_tex = self._texs[:3]

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
        for fbo, tex, (tw, th) in zip(self._fbos[:3], self._texs[:3], self._sizes):
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, tw, th, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0)
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("frost framebuffer incomplete")
        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        self._target_size = (width, height)
