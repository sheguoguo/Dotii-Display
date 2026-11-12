/* 由 scripts/generate_dotii_face_assets.py 生成，请勿手改。 */
#pragma once

#include "lvgl.h"

#define DOTII_FACE_COUNT 4

typedef struct {
    const lv_image_dsc_t *image;
    int x;
    int y;
} dotii_face_asset_t;

extern const dotii_face_asset_t dotii_face_assets[DOTII_FACE_COUNT];

enum {
    DOTII_FACE_SMILE = 0,
    DOTII_FACE_WINK,
    DOTII_FACE_COZY,
    DOTII_FACE_SPARKLE,
};
