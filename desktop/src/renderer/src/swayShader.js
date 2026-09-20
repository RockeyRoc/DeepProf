/**
 * 「摆动」滤镜 —— 让一张平铺的角色图产生有机的流动感。
 *
 * 原理：按像素在画面里的位置，给一个"形变权重"，再做横向波浪偏移。
 *   - 越靠下（裙摆、尾巴）权重越大 —— 摆动明显
 *   - 越靠上（脸、头）权重趋近 0 —— 保持稳定，脸绝不能歪
 *   - 左右边缘再补一点权重，让头发外侧轻轻飘
 *
 * 为什么需要它：
 *   分层素材还没到位（从梗图硬拆会带出别人的像素，已验证不可行）。
 *   这个方案不需要分层，用现有素材就能让角色动起来。
 *   等真正的分层素材到位，可以换成逐层骨骼动画 —— 到时候这个滤镜可以直接撤掉。
 *
 * ⚠️ 注意：Pixi 的 Filter 会用 new Function() 生成 uniform 代码，
 *    所以必须先 install(@pixi/unsafe-eval)，否则会被 CSP 拦下。
 */

/*
 * ⚠️ 不要自己写顶点着色器。
 * Pixi 6 里 aVertexPosition 是像素坐标、不是 0~1，自己算 vTextureCoord 很容易写错，
 * 表现是 WebGL 报 "useProgram: program not valid" 然后整屏不渲染。
 * 传 undefined 让 Pixi 用它自带的顶点着色器即可。
 */

export const swayFragment = `
  varying vec2 vTextureCoord;
  uniform sampler2D uSampler;
  uniform float uTime;      // 秒
  uniform float uAmp;       // 总振幅
  uniform float uTalking;   // 0~1，说话时加强

  void main(void) {
    vec2 uv = vTextureCoord;

    // 纵向权重：上部（脸/头）近乎不动，下部（裙摆/尾巴）摆动最大
    float wBottom = smoothstep(0.30, 1.05, uv.y);
    // 横向权重：左右外侧稍微飘一点，中间稳定
    float wEdge = smoothstep(0.12, 0.0, uv.x) + smoothstep(0.88, 1.0, uv.x);

    float weight = wBottom * 0.85 + wEdge * 0.25;

    // 两个不同频率的波叠加，避免机械感
    float t = uTime;
    float wave =
        sin(uv.y * 5.0 - t * 1.7) * 0.6
      + sin(uv.y * 11.0 + t * 2.6 + uv.x * 3.0) * 0.4;

    // 横向偏移 + 一点点纵向起伏
    float amp = uAmp * (1.0 + uTalking * 0.9);
    vec2 offset = vec2(wave * amp * weight, sin(t * 2.1 + uv.x * 4.0) * amp * 0.35 * wBottom);

    gl_FragColor = texture2D(uSampler, uv + offset);
  }
`
