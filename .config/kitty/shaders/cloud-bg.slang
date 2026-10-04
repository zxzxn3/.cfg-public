#language slang 2026
// cloud-bg —— 体积云背景。移植自 nimitz 的 "Protean clouds"
//   https://www.shadertoy.com/view/3l23Rh
//   作者 nimitz（twitter: @stormoid）；Onan 加过 timeScaling
//   许可 CC BY-NC-SA 3.0：署名、非商业、相同方式共享
//
// 本文件只算云，而且每隔 CLOUD_ROW_STRIDE 行才算一次，结果写进临时纹理 a；
// cloud-bg-apply.slang 再把相邻两条算过的行插值、铺到背景像素上。云是平滑场，
// 垂直插值几乎无损，所以敢抽样（实测三项开关全开约 7 倍，平均色差 0.52/255）。
//
// 注意这一趟是**整屏都算**的：背景判定在 apply 那一趟才做，所以文字像素也逃不掉。
// 这是它必须慢在 5fps 的根本原因。
//
// 移植时按原版语义处理的三处：M3/rot 写成行主序（GLSL 是列主序）、
// p*m3 与 p.xy*=rot(a) 写成 mul(M, v)、原版的可变全局 prm1/bsMo 改成参数传入。

import kitty_custom_shader_types;

// ── 省电开关（会被 cloud-bg.pipeline 里的 var 覆盖）──
static const int CLOUD_ROW_STRIDE = 4;   // 每几行算一次云，1 = 不抽样
static const int CLOUD_LIGHT_TAPS = 1;   // 光照用几个额外 map 采样，原版 2
static const int CLOUD_MAP_ITER = 4;     // 每步的形变迭代，原版 5
static const int CLOUD_STEPS = 48;       // 步进数，原版 130

// ── 观感参数 ──
static const float CLOUD_TIME_SCALE = 0.2;  // 原版 timeScaling，越大云漂得越快
static const bool CLOUD_MOUSE = false;      // 鼠标是否控制视角

// 实现细节
static const float3x3 M3 = float3x3(
     0.33338, -0.87887,  0.15162,
     0.56034,  0.32651,  0.69596,
    -0.71817, -0.15323,  0.61339) * 1.93;

static float2
rot_mul(float a, float2 v) {
    float c = cos(a), s = sin(a);
    return mul(float2x2(c, -s, s, c), v);
}

static float2
disp(float t) { return float2(sin(t * 0.22), cos(t * 0.175)) * 2.0; }

static float
mag2(float2 p) { return dot(p, p); }

static float
linstep(float mn, float mx, float x) { return clamp((x - mn) / (mx - mn), 0.0, 1.0); }

static float2
cloud_map(float3 p, float time, float prm1, float2 bsMo) {
    float3 p2 = p;
    p2.xy -= disp(p.z);
    p.xy = rot_mul(sin(p.z + time) * (0.1 + prm1 * 0.05) + time * 0.09, p.xy);
    float cl = mag2(p2.xy);
    float d = 0.0;
    p *= 0.61;
    float z = 1.0;
    float trk = 1.0;
    float dspAmp = 0.1 + prm1 * 0.2;
    for (int i = 0; i < CLOUD_MAP_ITER; i++) {
        p += sin(p.zxy * 0.75 * trk + time * trk * 0.8) * dspAmp;
        d -= abs(dot(cos(p), sin(p.yzx)) * z);
        z *= 0.57;
        trk *= 1.4;
        p = mul(M3, p);
    }
    d = abs(d + prm1 * 3.0) + prm1 * 0.3 - 2.5 + bsMo.y;
    return float2(d + cl * 0.2 + 0.25, cl);
}

static float4
cloud_render(float3 ro, float3 rd, float time, float prm1, float2 bsMo) {
    float4 rez = float4(0.0);
    float t = 1.5;
    float fogT = 0.0;
    for (int i = 0; i < CLOUD_STEPS; i++) {
        if (rez.a > 0.99) break;

        float3 pos = ro + t * rd;
        float2 mpv = cloud_map(pos, time, prm1, bsMo);
        float den = clamp(mpv.x - 0.3, 0.0, 1.0) * 1.12;
        float dn = clamp(mpv.x + 2.0, 0.0, 3.0);

        float4 col = float4(0.0);
        if (mpv.x > 0.6) {
            col = float4(sin(float3(5.0, 0.4, 0.2) + mpv.y * 0.1 + sin(pos.z * 0.4) * 0.5 + 1.8) * 0.5 + 0.5, 0.08);
            col *= den * den * den;
            col.rgb *= linstep(4.0, -2.5, mpv.x) * 2.3;
            float dif = clamp((den - cloud_map(pos + 0.8, time, prm1, bsMo).x) / 9.0, 0.001, 1.0);
            if (CLOUD_LIGHT_TAPS > 1) {   // 原版还有一次 pos+0.35 的采样
                dif += clamp((den - cloud_map(pos + 0.35, time, prm1, bsMo).x) / 2.5, 0.001, 1.0);
            }
            col.xyz *= den * (float3(0.005, 0.045, 0.075) + 1.5 * float3(0.033, 0.07, 0.03) * dif);
        }

        float fogC = exp(t * 0.2 - 2.2);
        col += float4(0.06, 0.11, 0.11, 0.1) * clamp(fogC - fogT, 0.0, 1.0);
        fogT = fogC;
        rez = rez + col * (1.0 - rez.a);
        t += clamp(0.5 - dn * dn * 0.05, 0.09, 0.3);
    }
    return clamp(rez, 0.0, 1.0);
}

static float
getsat(float3 c) {
    float mi = min(min(c.x, c.y), c.z);
    float ma = max(max(c.x, c.y), c.z);
    return (ma - mi) / (ma + 1e-7);
}

static float3
iLerp(float3 a, float3 b, float x) {
    float3 ic = lerp(a, b, x) + float3(1e-6, 0.0, 0.0);
    float sd = abs(getsat(ic) - lerp(getsat(a), getsat(b), x));
    float3 dir = normalize(float3(2.0 * ic.x - ic.y - ic.z,
                                  2.0 * ic.y - ic.x - ic.z,
                                  2.0 * ic.z - ic.y - ic.x));
    float lgt = dot(float3(1.0), ic);
    float ff = dot(dir, normalize(ic));
    ic += 1.5 * dir * sd * ff * lgt;
    return clamp(ic, 0.0, 1.0);
}

public float4
fragment_main(float4 color, KittyTextures t, KittyCustomShaderData d) {
    float2 res = max(float2(d.viewport_size_pixels) * t.viewport.zw, float2(1.0));

    // 行抽样：这一行不归本轮算，直接交回去（apply 那一趟只会采样算过的行）。
    // 这个分支对 GPU 很友好——一个 warp 覆盖的是同一行的连续像素，所以整块 warp 一起跳过。
    float yrow = floor(t.pos.y * res.y);
    // fmod 就是 HLSL/Slang 里的取余（GLSL 的 mod 对负数才另有定义，而行号恒非负）。
    if (CLOUD_ROW_STRIDE > 1 && fmod(yrow, float(CLOUD_ROW_STRIDE)) > 0.5) {
        return float4(0.0, 0.0, 0.0, 1.0);
    }

    float2 fragCoord = t.pos * res;
    float2 q = fragCoord / res;
    float2 p = (fragCoord - 0.5 * res) / res.y;

    float2 bsMo = CLOUD_MOUSE ? (d.mouse_pos.xy * res - 0.5 * res) / res.y : float2(0.0);

    float time = d.timestamp * CLOUD_TIME_SCALE * 3.0;
    float3 ro = float3(0.0, 0.0, time);
    ro += float3(sin(d.timestamp * CLOUD_TIME_SCALE) * 0.5, 0.0, 0.0);

    float dspAmp = 0.85;
    ro.xy += disp(ro.z) * dspAmp;

    float tgtDst = 3.5;
    float3 target = normalize(ro - float3(disp(time + tgtDst) * dspAmp, time + tgtDst));
    ro.x -= bsMo.x * 2.0;
    float3 rightdir = normalize(cross(target, float3(0.0, 1.0, 0.0)));
    float3 updir = normalize(cross(rightdir, target));
    rightdir = normalize(cross(updir, target));
    float3 rd = normalize(p.x * rightdir + p.y * updir - target);
    rd.xy = rot_mul(-disp(time + 3.5).x * 0.2 + bsMo.x, rd.xy);

    float prm1 = smoothstep(-0.4, 0.4, sin(d.timestamp * CLOUD_TIME_SCALE * 0.3));
    float4 scn = cloud_render(ro, rd, time, prm1, bsMo);

    float3 col = scn.rgb;
    col = iLerp(col.bgr, col.rgb, clamp(1.0 - prm1, 0.05, 1.0));
    col = pow(col, float3(0.55, 0.65, 0.6)) * float3(1.0, 0.97, 0.9);
    col *= pow(16.0 * q.x * q.y * (1.0 - q.x) * (1.0 - q.y), 0.12) * 0.7 + 0.3;  // 暗角

    // 只算，不上色——亮度乘数和背景混合在 cloud-bg-apply 那一趟做。
    return float4(col, 1.0);
}
