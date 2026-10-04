#language slang 2026
// focus-pulse —— 一个窗口获得焦点时，内容区边缘有一次很轻的"呼吸"。
//
// 单组、事件驱动、无状态，触发条件 os-window-focus-in（=这个 OS 窗口获得键盘焦点）。
// 附带的已知行为：kitty 在管线载入后的第一帧也会补发一次该事件，所以每次重载配置时
// 当前窗口也会脉动一下——正好当成"shader 已经重新加载"的信号。
// 带 PULSE_ 前缀的常量会被 pipeline 的组内 var 覆盖——调参改那个文件。

import kitty_custom_shader_types;

// 下面三个会被 focus-pulse.pipeline 的组内 var 覆盖
static const float PULSE_PEAK = 0.05;      // 最亮时叠多少（线性）
static const float PULSE_BAND_PX = 48.0;   // 从边缘往里多少像素衰减到没有
static const float PULSE_ATTACK = 0.12;    // 起手用掉整段动画的比例

public float4
fragment_main(float4 color, KittyTextures t, KittyCustomShaderData d) {
    float4 area = d.central_area;
    if (area.z <= 0.0 || area.w <= 0.0) return color;

    // 到内容区四条边的距离。x/y 分开换算成像素，非等比窗口下带宽才不会被拉歪。
    float2 dd = min(t.pos - area.xy, area.xy + area.zw - t.pos) * float2(d.viewport_size_pixels);
    float dmin = min(dd.x, dd.y);
    if (dmin <= 0.0) return color;   // 内容区之外（标签栏所在的那条）不加

    float band = 1.0 - smoothstep(0.0, PULSE_BAND_PX, dmin);
    if (band <= 0.0) return color;

    // envelope：平滑起手，然后落到 0。progress 由 pipeline 给成线性的，
    // 所以这里的形状就是最终看到的形状。
    float p = clamp(t.animation_progress, 0.0, 1.0);
    float env = (1.0 - p) * smoothstep(0.0, PULSE_ATTACK, p);
    if (env <= 0.0) return color;

    return float4(color.rgb + float3(band * env * PULSE_PEAK), color.a);
}
