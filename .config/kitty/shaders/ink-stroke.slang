#language slang 2026
// ink-stroke —— 光标移动时留下一道柔和的白色笔触。无状态。
//
// 每一帧只按当前拖尾几何画一笔，淡出交给 kitty 自己的 cursor_trail_decay，
// 所以不需要 persist / commit / 时间。代价：墨不会留在屏幕上（淡出快慢由
// cursor_trail_decay 决定，不能越积越浓），起笔也是立即的、没有渐入。
// 只落在背景像素上，字一个像素都不碰。

import kitty_custom_shader_types;

// 最浓时往墨色混多少。会被 ink-stroke.pipeline 里的 var 覆盖。
static const float INK_STRENGTH = 0.12;
// 墨色。
static const float3 INK_COLOR = float3(1.0);
// 笔尖半径，像素。往外是软边。
static const float NIB_RADIUS = 15.0;
// 笔尖中心这一圈占半径的比例，圈内是实心的。
static const float NIB_CORE = 0.25;
// 判定「这是背景」的色彩容差（线性 RGB）。
static const float BG_TOLERANCE = 0.02;
// 沾了墨的地方不透明度往 1 靠多少。
static const float INK_ALPHA_BOOST = 0.50;

static float
seg_dist(float2 p, float2 a, float2 b) {
    float2 ab = b - a;
    float t = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-7), 0.0, 1.0);
    return length(p - (a + ab * t));
}

public float4
fragment_main(float4 color, KittyTextures t, KittyCustomShaderData d) {
    // 拖尾活着的时候才有光标坐标；它自己的衰减决定这一笔现在多淡。
    float life = d.cursor_trail_state * d.cursor_trail_color.a;
    if (life <= 0.0) return color;

    // 只压在背景上，字不碰。
    float3 diff = abs(color.rgb - d.background.rgb);
    float bg = 1.0 - smoothstep(0.0, BG_TOLERANCE, max(max(diff.r, diff.g), diff.b));
    if (bg <= 0.0) return color;

    float2 dims = float2(d.viewport_size_pixels) * t.viewport.zw;
    float2 pen = t.pos * dims;
    float2 cur = float2((d.cursor_trail_edge.x + d.cursor_trail_edge.y) * 0.5,
                        (d.cursor_trail_edge.z + d.cursor_trail_edge.w) * 0.5) * dims;
    float2 prev = float2((d.cursor_trail_prev_edge.x + d.cursor_trail_prev_edge.y) * 0.5,
                         (d.cursor_trail_prev_edge.z + d.cursor_trail_prev_edge.w) * 0.5) * dims;
    // 光标第一次移动时 prev_edge 还是全零，直接用它会在左下角拉出一条长划。
    float prev_ok = step(1e-5, d.cursor_trail_prev_edge.x + d.cursor_trail_prev_edge.y
                               + d.cursor_trail_prev_edge.z + d.cursor_trail_prev_edge.w);
    prev = lerp(cur, prev, prev_ok);

    // 笔尖粗细跟着光标本身的大小走，但不小于一个下限。
    float nib = max(NIB_RADIUS, (d.cursor_trail_edge.y - d.cursor_trail_edge.x) * dims.x * 1.5);
    float k = (1.0 - smoothstep(nib * NIB_CORE, nib, seg_dist(pen, prev, cur))) * INK_STRENGTH * life;
    if (k <= 0.0) return color;
    return float4(lerp(color.rgb, INK_COLOR, k), lerp(color.a, 1.0, k * INK_ALPHA_BOOST));
}
