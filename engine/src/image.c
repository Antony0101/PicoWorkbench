/* Bounded flash/UF2 ingestion. No writes occur until the complete image validates. */
#include "sim_internal.h"
#include <stdlib.h>
#include <string.h>

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int sim_load_flash(sim_t *s, const uint8_t *data, size_t length, const uint8_t *rom, size_t rom_length) {
    if (!s || !data || !rom) return -1;
    if (length < 256 || length > FLASH_SIZE) return sim_fail(s, "Flash image must contain 256 bytes to 2 MiB");
    if (rom_length != 0x4000 || memcmp(rom + 0x10, "Mu\1\3", 4))
        return sim_fail(s, "Expected a 16 KiB RP2040 B2 boot ROM");
    uint32_t sp = get32(rom), entry = get32(rom + 4);
    if ((sp & 7) || sp <= RAM_BASE || sp > RAM_BASE + RAM_SIZE || !(entry & 1) || entry >= 0x4000)
        return sim_fail(s, "Invalid boot ROM reset vectors");
    uint8_t *copy = malloc(0x4000);
    if (!copy) return sim_fail(s, "Cannot allocate boot ROM image");
    memcpy(copy, rom, 0x4000); free(s->rom); s->rom = copy;
    memset(s->flash, 0xff, FLASH_SIZE); memcpy(s->flash, data, length);
    memset(s->ram, 0, RAM_SIZE);
    s->rom_mode = true; s->sdk_mode = false; s->loaded = 1;
    s->entry = entry; s->stack = sp; s->vector_base = 0;
    s->segments[0] = (segment_t){FLASH_BASE, FLASH_SIZE, 5};
    s->segments[1] = (segment_t){RAM_BASE, RAM_SIZE, 7}; s->segment_count = 2;
    return sim_reset(s);
}

int sim_load_uf2(sim_t *s, const uint8_t *data, size_t length, const uint8_t *rom, size_t rom_length) {
    if (!s || !data || !rom) return -1;
    if (!length || length % 512 || length > FLASH_SIZE * 4u)
        return sim_fail(s, "UF2 must contain complete 512-byte blocks, at most 8 MiB");
    uint8_t *image = malloc(FLASH_SIZE);
    uint32_t *numbers = calloc(FLASH_SIZE / 256, sizeof(uint32_t));
    uint8_t *pages = calloc(FLASH_SIZE / 256, 1);
    if (!image || !numbers || !pages) {
        free(image); free(numbers); free(pages); return sim_fail(s, "Cannot allocate UF2 image");
    }
    memset(image, 0xff, FLASH_SIZE);
    uint32_t expected = 0, count = 0;
    const char *error = NULL;
    for (size_t offset = 0; offset < length; offset += 512) {
        const uint8_t *b = data + offset;
        if (get32(b) != 0x0a324655 || get32(b + 4) != 0x9e5d5157 || get32(b + 508) != 0x0ab16f30) { error = "Invalid UF2 block magic"; break; }
        if (get32(b + 8) != 0x2000 || get32(b + 28) != 0xe48bff56) { error = "UF2 requires RP2040 family ID and flash-only flags"; break; }
        uint32_t addr = get32(b + 12), size = get32(b + 16), no = get32(b + 20), total = get32(b + 24);
        if (size != 256 || (addr & 255) || addr < FLASH_BASE || (uint64_t)addr + 256 > FLASH_BASE + FLASH_SIZE) { error = "Invalid or out-of-bounds UF2 flash page"; break; }
        if (!total || total > FLASH_SIZE / 256 || no >= total || (expected && total != expected)) { error = "Invalid UF2 block numbering"; break; }
        expected = total;
        unsigned page = (addr - FLASH_BASE) / 256;
        if (numbers[no]) {
            if (numbers[no] != page + 1 || memcmp(image + page * 256, b + 32, 256)) { error = "Conflicting duplicate UF2 block"; break; }
            continue;
        }
        if (pages[page]) { error = "Overlapping UF2 flash pages"; break; }
        pages[page] = 1; numbers[no] = page + 1; count++;
        memcpy(image + page * 256, b + 32, 256);
    }
    if (!error && count != expected) error = "Incomplete UF2 image";
    if (!error && !pages[0]) error = "UF2 image has no boot2 page";
    int result = error ? sim_fail(s, "%s", error) : sim_load_flash(s, image, FLASH_SIZE, rom, rom_length);
    free(image); free(numbers); free(pages); return result;
}
