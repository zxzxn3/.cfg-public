#language slang 2026
// star-hash —— 哈希星场背景（两层视差版）
//
// 格子哈希撒星星：像素按格子边长切开，每格用哈希决定这颗星存不存在、落在格子哪一点、
// 多亮、多大、偏冷还是偏暖，然后只算自己的 3×3 邻域，按距离衰减成小圆点。
// 同一套逻辑跑两遍就是视差：近景层格子大、星点大、亮、全速漂；远景层细一半、暗一半、
// 只走 STAR_FAR_SPEED 倍的速度。
//
// 哈希函数来自 Dave Hoskins 的 "Hash without Sine"（MIT）。
// 只画背景像素（和背景色比色），所以和另外几个背景互斥。

import kitty_custom_shader_types;

// ── 旋钮（在 star-hash.pipeline 里覆盖）──
static const float STAR_CELL = 26.0;      // 近景层格子边长（像素）。越小星越密
static const float STAR_DENSITY = 0.35;   // 近景层有多大比例的格子有星
static const float STAR_BRIGHT = 0.55;    // 近景层最亮的星（线性亮度，叠在背景上）
static const float STAR_SPEED = 120.0;    // 近景层漂移速度（像素/秒），0 = 完全静止
static const float STAR_FAR_SCALE = 0.55; // 远景层：格子/半径/亮度都乘这个（越小越细越暗）
static const float STAR_FAR_SPEED = 0.35; // 远景层的速度比例 —— **这个就是视差强度**

// ── 实现细节 ──
static const float STAR_RADIUS = 2.2;     // 近景层星点基准半径（像素）
static const float STAR_FAR_DENSITY = 0.30; // 远景层的格子密度
static const float STAR_BAND = 0.45;      // 大尺度疏密（银河带的雏形）
static const float BG_TOLERANCE = 0.02;

static float
hash21(float2 p) {
    float3 p3 = frac(float3(p.x, p.y, p.x) * 0.1031);
    p3 += dot(p3, float3(p3.y, p3.z, p3.x) + 33.33);
    return frac((p3.x + p3.y) * p3.z);
}

static float3
star_tint(float t) {
    // 0 = 暖白（偏橘），1 = 冷白（偏蓝）
    return lerp(float3(1.0, 0.86, 0.70), float3(0.78, 0.88, 1.0), t);
}

// 一层星：在 sky 空间里按 cell_px 切格子，撒一层星点
static float3
star_layer(float2 sky, float cell_px, float density, float radius_px, float bright, float seed) {
    float2 cell = floor(sky / cell_px);
    float2 fx = sky - cell * cell_px;                 // 像素落在自己这一格里的位置
    float rmax = radius_px * 1.3;                     // 星的最大半径（下面 radius 那行的上限）
    // 到左/中/右三个邻居格的横向间隙（都 ≥ 0），纵向同理。间隙是可分的，
    // 所以一次算 6 个数就够 3×3 用；够不着圆形的格子连哈希都不必算。
    float3 gx = float3(fx.x, 0.0, cell_px - fx.x);
    float3 gy = float3(fx.y, 0.0, cell_px - fx.y);
    float rmax2 = rmax * rmax;
    float3 acc = float3(0.0);
    for (int j = -1; j <= 1; j++) {
        for (int i = -1; i <= 1; i++) {
            if (gx[i + 1] * gx[i + 1] + gy[j + 1] * gy[j + 1] > rmax2) continue;
            float2 c = cell + float2(i, j) + seed;           // seed 让两层用互不相关的格子
            if (hash21(c) > density) continue;               // 大部分格子是空的
            float2 jitter = float2(hash21(c + 11.7), hash21(c + 23.1));
            float2 delta = sky - ((cell + float2(i, j)) + jitter) * cell_px;
            float mag = hash21(c + 53.9);
            float radius = radius_px * (0.55 + 0.75 * mag);  // 亮的也大一点
            float d2 = dot(delta, delta);
            if (d2 > radius * radius) continue;              // 够不着，早退
            // 三次衰减的小圆点：中心最亮、边缘平滑收到 0
            float profile = 1.0 - d2 / (radius * radius);
            acc += (profile * profile * profile) * (0.22 + 0.78 * mag * mag * mag)
                   * star_tint(hash21(c + 71.3)) * bright;
        }
    }
    return acc;
}

public float4
fragment_main(float4 color, KittyTextures t, KittyCustomShaderData d) {
    float3 diff = abs(color.rgb - d.background.rgb);
    float bg = 1.0 - smoothstep(0.0, BG_TOLERANCE, max(max(diff.r, diff.g), diff.b));
    if (bg <= 0.0) return color;

    float2 res = max(float2(d.viewport_size_pixels) * t.viewport.zw, float2(1.0));
    float2 base = t.pos * res;                              // 星场锚在像素上（各向同性）
    float2 drift = float2(-0.35, -0.94) * (d.timestamp * STAR_SPEED);

    float3 stars = star_layer(base + drift, STAR_CELL, STAR_DENSITY, STAR_RADIUS, 1.0, 0.0)
                 + star_layer(base + drift * STAR_FAR_SPEED,
                              STAR_CELL * STAR_FAR_SCALE, STAR_FAR_DENSITY,
                              STAR_RADIUS * STAR_FAR_SCALE, STAR_FAR_SCALE, 37.0);
    // 星点只盖住一小部分像素。没星就不算大尺度疏密那三个三角函数。
    if (stars.r + stars.g + stars.b <= 0.0) return color;

    // 大尺度疏密：两层共用一个场，免得两层各漂各的、看着乱
    float2 q = (base + drift * 0.5) / 700.0;
    float band = 1.0 + STAR_BAND * (sin(q.x * 2.1 + 1.7) * cos(q.y * 1.7 - 0.4)
                                    + 0.5 * sin((q.x + q.y) * 3.1));

    // 加法叠在背景上：背景该多黑还是多黑，星星浮在上面
    return float4(lerp(color.rgb, color.rgb + stars * (STAR_BRIGHT * band), bg), color.a);
}
