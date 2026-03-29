// doomgeneric platform implementation for openpilot
// Provides callbacks + helper functions callable from Python via ctypes

#include "doomgeneric_src/doomgeneric/doomgeneric.h"
#include "doomgeneric_src/doomgeneric/doomkeys.h"

#include <string.h>
#include <unistd.h>
#include <sys/time.h>
#include <setjmp.h>
#include <stdint.h>

// --- Key Queue ---
#define KEYQUEUE_SIZE 32

static unsigned short s_KeyQueue[KEYQUEUE_SIZE];
static unsigned int s_KeyQueueWriteIndex = 0;
static unsigned int s_KeyQueueReadIndex = 0;

// --- RGBA framebuffer (converted from DOOM's XRGB) ---
static uint32_t s_rgba_buffer[DOOMGENERIC_RESX * DOOMGENERIC_RESY];
static volatile int s_frame_ready = 0;

// --- Exit handling via setjmp/longjmp ---
static jmp_buf s_jmp_buf;
static volatile int s_has_exited = 0;
static int s_exit_code = 0;

// Override exit() - compiled with -Dexit=doom_exit
void doom_exit(int code) {
    s_exit_code = code;
    s_has_exited = 1;
    longjmp(s_jmp_buf, 1);
}

// --- Public API for Python ---

void doom_add_key(int pressed, unsigned char key) {
    unsigned short keyData = (unsigned short)((pressed << 8) | key);
    s_KeyQueue[s_KeyQueueWriteIndex] = keyData;
    s_KeyQueueWriteIndex = (s_KeyQueueWriteIndex + 1) % KEYQUEUE_SIZE;
}

uint32_t* doom_get_rgba_buffer(void) {
    return s_rgba_buffer;
}

int doom_get_resx(void) {
    return DOOMGENERIC_RESX;
}

int doom_get_resy(void) {
    return DOOMGENERIC_RESY;
}

int doom_frame_ready(void) {
    int ready = s_frame_ready;
    s_frame_ready = 0;
    return ready;
}

int doom_has_exited(void) {
    return s_has_exited;
}

// Safe wrapper for doomgeneric_Create - catches exit() calls
int doom_create(int argc, char** argv) {
    s_has_exited = 0;
    s_exit_code = 0;
    s_frame_ready = 0;
    s_KeyQueueWriteIndex = 0;
    s_KeyQueueReadIndex = 0;

    if (setjmp(s_jmp_buf) == 0) {
        doomgeneric_Create(argc, argv);
        return 0;
    } else {
        return s_exit_code != 0 ? s_exit_code : -1;
    }
}

// Safe wrapper for doomgeneric_Tick - catches exit() calls
int doom_tick(void) {
    if (s_has_exited) return -1;

    if (setjmp(s_jmp_buf) == 0) {
        doomgeneric_Tick();
        return 0;
    } else {
        return s_exit_code != 0 ? s_exit_code : -1;
    }
}

// --- DG Callback Implementations ---

void DG_Init() {
    memset(s_KeyQueue, 0, sizeof(s_KeyQueue));
    memset(s_rgba_buffer, 0, sizeof(s_rgba_buffer));
}

void DG_DrawFrame() {
    // Convert XRGB8888 (0x00RRGGBB) -> RGBA8888 for raylib (little-endian: 0xAABBGGRR)
    pixel_t* src = DG_ScreenBuffer;
    uint32_t* dst = s_rgba_buffer;
    int count = DOOMGENERIC_RESX * DOOMGENERIC_RESY;

    for (int i = 0; i < count; i++) {
        uint32_t pixel = src[i];
        uint8_t r = (pixel >> 16) & 0xFF;
        uint8_t g = (pixel >> 8) & 0xFF;
        uint8_t b = pixel & 0xFF;
        dst[i] = (uint32_t)r | ((uint32_t)g << 8) | ((uint32_t)b << 16) | (0xFFu << 24);
    }

    s_frame_ready = 1;
}

void DG_SleepMs(uint32_t ms) {
    if (ms > 0) {
        usleep(ms * 1000);
    }
}

uint32_t DG_GetTicksMs() {
    struct timeval tp;
    gettimeofday(&tp, NULL);
    return (uint32_t)(tp.tv_sec * 1000 + tp.tv_usec / 1000);
}

int DG_GetKey(int* pressed, unsigned char* doomKey) {
    if (s_KeyQueueReadIndex == s_KeyQueueWriteIndex) {
        return 0;
    }

    unsigned short keyData = s_KeyQueue[s_KeyQueueReadIndex];
    s_KeyQueueReadIndex = (s_KeyQueueReadIndex + 1) % KEYQUEUE_SIZE;

    *pressed = keyData >> 8;
    *doomKey = keyData & 0xFF;

    return 1;
}

void DG_SetWindowTitle(const char* title) {
    (void)title;
}
