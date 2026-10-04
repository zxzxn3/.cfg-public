#language slang 2026
// crt-warp —— 只保留 CRT 的视场扭曲（屏幕鼓起），扫描线、染色、辉光、暗角一概不做。
//
// 纯位移采样：按与中心的距离把 UV 往外推一点，再拿 backbuffer 在那个位置的颜色填回来。
// 保留采样到的 alpha，所以 background_opacity < 1 仍然可用 —— kitty 自带的
// crt.slang 末尾写死 alpha=1，直接用它会让整个窗口变成不透明。
// 曲率从 crt-warp.pipeline 的组内 var 覆盖，调参改那边，别动这里。

import kitty_custom_shader_types;

// 曲率强度。kitty 自带 crt 的默认值就是 0.25。
static const float WARP = 0.25f;
// 两个轴各自的弯曲比例。kitty 自带的是 0.3 / 0.4：上下方向弯得更多一点，
// 因为终端格子通常竖长，纵向要更狠才显得球面对称。
static const float WARP_X = 0.3f;
static const float WARP_Y = 0.4f;

public float4
fragment_main(float4 color, KittyTextures t, KittyCustomShaderData d) {
    // 视口内的 UV，0..1。
    float2 uv = t.pos;

    // 到中心的距离平方：离中心越远推得越狠，这就是"视场被扭起来"的来源。
    float2 dc = abs(0.5f - uv);
    dc *= dc;

    uv.x = 0.5f + (uv.x - 0.5f) * (1.0f + dc.y * WARP_X * WARP);
    uv.y = 0.5f + (uv.y - 0.5f) * (1.0f + dc.x * WARP_Y * WARP);

    // 四角会略微采样到视口外，Sampler 自带 clamp（见 kitty 的 pipeline.slang），
    // 所以边缘是拉丝而不是黑边 —— 纯位移、不叠暗角的必然结果。
    return t.backbuffer.Sample(uv);
}
