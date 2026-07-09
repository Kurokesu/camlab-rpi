"""Frost-capable subclass of the picamera2 GL preview widget.

Adds set_frosted(bool): when on, each frame is drawn through a GPU blur
instead of the passthrough shader, so the preview stays live but frosted.
The blur is a dual-pass chain sized for a frost-strength radius at negligible
cost: downsample the camera texture to 1/4 then 1/8 into offscreen buffers
(linear filtering does the averaging), separable 9-tap Gaussian iterations at
1/8 scale, then upsample to the screen. A full-resolution Gaussian of the
same apparent radius would need hundreds of taps per pixel.

Integration piggybacks on the base repaint(): the blur chain renders into a
small texture, which is pre-bound to the unit's GL_TEXTURE_2D target, and
program_image is swapped for the upsample program for the duration of the
super() call. The base class binds the camera dmabuf to the same unit's
GL_TEXTURE_EXTERNAL_OES target, but a sampler only reads its own target, so
its draw samples our blurred texture. Everything else (letterbox viewport,
clear, overlay pass, buffer swap) stays upstream code.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

from OpenGL.EGL.VERSION.EGL_1_0 import EGL_NO_CONTEXT, EGL_NO_SURFACE, eglMakeCurrent
from OpenGL.GL import shaders
from OpenGL.GLES2.OES.EGL_image_external import GL_TEXTURE_EXTERNAL_OES
from OpenGL.GLES2.VERSION.GLES2_2_0 import (
    GL_CLAMP_TO_EDGE,
    GL_COLOR_ATTACHMENT0,
    GL_FALSE,
    GL_FLOAT,
    GL_FRAGMENT_SHADER,
    GL_FRAMEBUFFER,
    GL_FRAMEBUFFER_COMPLETE,
    GL_LINEAR,
    GL_RGBA,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S,
    GL_TEXTURE_WRAP_T,
    GL_TRIANGLE_FAN,
    GL_UNSIGNED_BYTE,
    GL_VERTEX_SHADER,
    glBindFramebuffer,
    glBindTexture,
    glCheckFramebufferStatus,
    glDeleteTextures,
    glDrawArrays,
    glEnableVertexAttribArray,
    glFramebufferTexture2D,
    glGenFramebuffers,
    glGenTextures,
    glGetAttribLocation,
    glGetUniformLocation,
    glTexImage2D,
    glTexParameteri,
    glUniform1i,
    glUniform2f,
    glUseProgram,
    glVertexAttribPointer,
    glViewport,
)

log = logging.getLogger(__name__)

# H+V Gaussian iterations at 1/8 scale. Each adds ~sigma 21 px (full-res
# equivalent), two together read close to the PIL frost this replaces.
_BLUR_PASSES = 2

# Identity vertex shader (texcoord = quad position). Any hflip/vflip transform
# is applied once in the first pass, which reuses upstream's flipping shader.
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


def _compile(src: str, kind) -> object:
    sh = shaders.compileShader(src, kind)
    # compileShader occasionally returns a 1-tuple (see upstream q_gl_picamera2).
    return sh[0] if isinstance(sh, tuple) else sh


def frost_widget_class(base):
    """Wrap a QGlPicamera2-compatible class with live-frost support."""

    class FrostGlPreview(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._frosted = False
            self._frost_ready = False
            self._frost_broken = False
            self._quad = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
            self._target_size: tuple[int, int] | None = None

        def set_frosted(self, frosted: bool) -> None:
            """Blur (True) or passthrough (False) rendering of the live stream."""
            frosted = bool(frosted) and not self._frost_broken
            if frosted == self._frosted:
                return
            self._frosted = frosted
            if not self.running or self.surface is None:
                return
            # Same context dance as upstream set_overlay: make current, redraw
            # the held request so the change shows without waiting for a frame.
            with self.lock:
                eglMakeCurrent(self.egl.display, self.surface, self.surface,
                               self.egl.context)
                try:
                    self.repaint(self.current_request)
                finally:
                    eglMakeCurrent(self.egl.display, EGL_NO_SURFACE,
                                   EGL_NO_SURFACE, EGL_NO_CONTEXT)

        def repaint(self, completed_request, update_viewport=False):
            if self._frosted and completed_request is not None:
                try:
                    self._repaint_frosted(completed_request, update_viewport)
                    return
                except Exception:
                    # A broken frost must never kill the preview: log once,
                    # fall through to the stock sharp path for good.
                    log.exception("frost render failed, disabling")
                    self._frost_broken = True
                    self._frosted = False
                    # The chain may have died with an FBO bound or the
                    # viewport pointed at a blur target, so reset both.
                    glBindFramebuffer(GL_FRAMEBUFFER, 0)
                    update_viewport = True
            super().repaint(completed_request, update_viewport)

        def _repaint_frosted(self, completed_request, update_viewport) -> None:
            # Mirror upstream's buffer bookkeeping for requests we see first.
            if completed_request.request not in self.buffers:
                if self.stop_count != self.picamera2.stop_count:
                    for _, buffer in self.buffers.items():
                        glDeleteTextures(1, [buffer.texture])
                    self.buffers = {}
                    self.stop_count = self.picamera2.stop_count
                self.buffers[completed_request.request] = self.Buffer(
                    self.egl.display, completed_request, self.egl.max_texture_size)
            camera_texture = self.buffers[completed_request.request].texture

            if not self._frost_ready:
                self._init_frost_gl()
            cfg = self.picamera2.stream_map[
                self.picamera2.camera_config['display']].configuration
            self._ensure_targets(cfg.size.width, cfg.size.height)

            (aw, ah), (bw, bh) = self._sizes[0], self._sizes[1]
            a_fbo, b_fbo, c_fbo = self._fbos
            a_tex, b_tex, c_tex = self._texs

            # camera (external) -> A at 1/4
            glBindFramebuffer(GL_FRAMEBUFFER, a_fbo)
            glViewport(0, 0, aw, ah)
            glUseProgram(self._prog_ext)
            glBindTexture(GL_TEXTURE_EXTERNAL_OES, camera_texture)
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
            # A -> B at 1/8
            glBindFramebuffer(GL_FRAMEBUFFER, b_fbo)
            glViewport(0, 0, bw, bh)
            glUseProgram(self._prog_copy)
            glBindTexture(GL_TEXTURE_2D, a_tex)
            glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
            # Gaussian ping-pong B <-> C, result lands back in B
            glUseProgram(self._prog_blur)
            for _ in range(_BLUR_PASSES):
                glBindFramebuffer(GL_FRAMEBUFFER, c_fbo)
                glBindTexture(GL_TEXTURE_2D, b_tex)
                glUniform2f(self._blur_step, 1.0 / bw, 0.0)
                glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
                glBindFramebuffer(GL_FRAMEBUFFER, b_fbo)
                glBindTexture(GL_TEXTURE_2D, c_tex)
                glUniform2f(self._blur_step, 0.0, 1.0 / bh)
                glDrawArrays(GL_TRIANGLE_FAN, 0, 4)
            glBindFramebuffer(GL_FRAMEBUFFER, 0)

            # Screen pass via super().repaint() with the upsample program in
            # place of the passthrough. Force the viewport: blur passes above
            # clobbered the letterbox one it otherwise reuses.
            glBindTexture(GL_TEXTURE_2D, b_tex)
            stock_program = self.program_image
            self.program_image = self._prog_copy
            try:
                super().repaint(completed_request, update_viewport=True)
            finally:
                self.program_image = stock_program

        def _init_frost_gl(self) -> None:
            # Pass 1 reuses upstream's vertex source so hflip/vflip transforms
            # apply exactly once (identity shaders everywhere after).
            vert_ext = f"""
                attribute vec2 aPosition;
                varying vec2 texcoord;

                void main()
                {{
                    gl_Position = vec4(aPosition * 2.0 - 1.0, 0.0, 1.0);
                    texcoord.x = {'1.0 - ' if self.transform.hflip else ''}aPosition.x;
                    texcoord.y = {'' if self.transform.vflip else '1.0 - '}aPosition.y;
                }}
            """
            self._prog_ext = self._build_program(vert_ext, _FRAG_EXT)
            self._prog_copy = self._build_program(_VERT_PLAIN, _FRAG_2D)
            self._prog_blur = self._build_program(_VERT_PLAIN, _FRAG_BLUR)
            self._blur_step = glGetUniformLocation(self._prog_blur, "texel")
            self._fbos = [int(f) for f in glGenFramebuffers(3)]
            self._texs = [int(t) for t in glGenTextures(3)]
            self._frost_ready = True

        def _build_program(self, vsrc: str, fsrc: str):
            prog = shaders.compileProgram(_compile(vsrc, GL_VERTEX_SHADER),
                                          _compile(fsrc, GL_FRAGMENT_SHADER))
            # Attribute arrays are per-index context state, so point each
            # program's aPosition at the quad (indices may overlap, harmless).
            loc = glGetAttribLocation(prog, "aPosition")
            glVertexAttribPointer(loc, 2, GL_FLOAT, GL_FALSE, 0, self._quad)
            glEnableVertexAttribArray(loc)
            glUseProgram(prog)
            glUniform1i(glGetUniformLocation(prog, "tex"), 0)
            return prog

        def _ensure_targets(self, width: int, height: int) -> None:
            """(Re)allocate offscreen textures when the display stream size changes."""
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
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            self._target_size = (width, height)

    return FrostGlPreview
