#include "usb_transport.h"
#include <string.h>
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_rom_crc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define MAX_FRAME (2 * 1024 * 1024)
static QueueHandle_t s_frames;
static SemaphoreHandle_t s_write_lock;

/* USB 数据通道在本定制版中停用，接收线程保留但不再启动；
 * 标记 __attribute__((unused)) 以避免 -Wunused-function 报错。 */
__attribute__((unused)) static bool read_exact(void *destination, size_t length)
{
    uint8_t *data = destination;
    size_t offset = 0;
    unsigned idle = 0;
    while (offset < length) {
        int n = usb_serial_jtag_read_bytes(data + offset, length - offset, pdMS_TO_TICKS(100));
        if (n > 0) { offset += n; idle = 0; }
        else if (++idle > 50) return false;
    }
    return true;
}

__attribute__((unused)) static void receiver(void *argument)
{
    (void)argument;
    unsigned magic = 0;
    while (true) {
        uint8_t byte;
        if (!read_exact(&byte, 1)) { magic = 0; continue; }
        if (byte == (uint8_t)"DTU1"[magic]) magic++;
        else magic = byte == 'D' ? 1 : 0;
        if (magic != 4) continue;
        magic = 0;
        uint32_t header[2];
        if (!read_exact(header, sizeof(header)) || header[0] == 0 || header[0] > MAX_FRAME) continue;
        char *body = heap_caps_malloc(header[0] + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!body) continue;
        if (read_exact(body, header[0]) && esp_rom_crc32_le(0, (const uint8_t *)body, header[0]) == header[1]) {
            body[header[0]] = '\0';
            if (xQueueSend(s_frames, &body, pdMS_TO_TICKS(100)) == pdTRUE) continue;
        }
        heap_caps_free(body);
    }
}

esp_err_t usb_transport_start(void)
{
    /* 本定制版不启用 USB 数据通道（connectivity_start_usb 不再调用本函数）。
     * 保留实现以便需要时恢复；这里不加锁也不建队列，直接返回未初始化状态，
     * 于是 usb_transport_receive() 永远返回 NULL、send() 变成空操作。 */
    return ESP_OK;
}

cJSON *usb_transport_receive(void)
{
    char *body;
    if (!s_frames || xQueueReceive(s_frames, &body, 0) != pdTRUE) return NULL;
    cJSON *message = cJSON_Parse(body);
    heap_caps_free(body);
    return message;
}

void usb_transport_send(cJSON *message)
{
    char *body = cJSON_PrintUnformatted(message);
    if (!body) return;
    size_t length = strlen(body);
    uint32_t header[2] = {(uint32_t)length, esp_rom_crc32_le(0, (const uint8_t *)body, length)};
    if (s_write_lock && xSemaphoreTake(s_write_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
        usb_serial_jtag_write_bytes("DTU1", 4, pdMS_TO_TICKS(500));
        usb_serial_jtag_write_bytes(header, sizeof(header), pdMS_TO_TICKS(500));
        usb_serial_jtag_write_bytes(body, length, pdMS_TO_TICKS(500));
        xSemaphoreGive(s_write_lock);
    }
    cJSON_free(body);
}
