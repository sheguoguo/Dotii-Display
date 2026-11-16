/* 由 scripts/generate_clock_face.py 生成，请勿手改。 */
#pragma once

#include "lvgl.h"

#define CLOCK_FACE_W 466
#define CLOCK_FACE_H 466

/* 奶油主题静态底图：渐变 + 高光 + 暗角 + 刻度 + 轨道环 + 兔兔 */
extern const lv_image_dsc_t clock_face_dsc;

/* 时间/日期文字的基线落点（与参考页 SVG 的 y 值一致） */
#define CLOCK_TIME_BASELINE 325
#define CLOCK_DATE_BASELINE 379
