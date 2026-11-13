/* 由 scripts/generate_clock_digits.py 生成，请勿手改。 */
#pragma once

#include "lvgl.h"

#define CLOCK_DIGIT_W 95
#define CLOCK_DIGIT_H 117

/* hours = 橙色渐变（时），minutes = 绿色渐变（分） */
extern const lv_image_dsc_t *const clock_digit_hours[10];
extern const lv_image_dsc_t *const clock_digit_minutes[10];
