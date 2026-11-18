#include "sim_internal.h"
#include <string.h>

static uint32_t bus_read(sim_t *s, uint32_t a, bool peek, bool *available) {
    bool handled;
    uint32_t value = sim_gpio_read(s, a, &handled);
    if (!handled) value = sim_peripherals_read(s, a, peek, &handled);
    if (!handled) value = sim_cpu_read(s, a, peek, &handled);
    if (!handled) value = sim_pio_read(s, a, peek, &handled);
    if (!handled) value = sim_dma_read(s, a, &handled);
    if (!handled) value = sim_multicore_read(s, a, peek, &handled);
    if (!handled) value = sim_system_read(s, a, peek, &handled);
    if (!handled) value = sim_flash_read(s, a, peek, &handled);
    if (!handled) value = sim_rtc_read(s, a, peek, &handled);
    if (available) *available = handled;
    return value;
}

static bool bus_write(sim_t *s, uint32_t a, uint32_t value) {
    if (sim_system_held(s, a)) return true;
    if (sim_gpio_write(s, a, value) || sim_peripherals_write(s, a, value) ||
        sim_cpu_write(s, a, value) || sim_pio_write(s, a, value) || sim_dma_write(s, a, value) ||
        sim_multicore_write(s, a, value) || sim_system_write(s, a, value) || sim_flash_write(s, a, value) || sim_rtc_write(s, a, value)) return true;
    return false;
}

static uint64_t read_callback(uc_engine *cpu, uint64_t off, unsigned size, void *data) {
    (void)cpu;
    region_t *r = data;
    if ((size != 1 && size != 2 && size != 4) || (off & (size - 1))) { sim_fail(r->sim, "MMIO requires aligned byte, halfword or word reads at 0x%08x (size %u)", r->base + (uint32_t)off, size); return 0; }
    bool available;
    uint32_t value = bus_read(r->sim, r->base + ((uint32_t)off & ~3u), false, &available);
    if (!available) sim_fail(r->sim, "Unsupported MMIO read at 0x%08x", r->base + (uint32_t)off);
    value >>= (off & 3) * 8;
    return size == 4 ? value : value & ((1u << (size * 8)) - 1);
}

static void write_callback(uc_engine *cpu, uint64_t off, unsigned size, uint64_t value, void *data) {
    (void)cpu;
    region_t *r = data;
    if ((size != 1 && size != 2 && size != 4) || (off & (size - 1))) { sim_fail(r->sim, "MMIO requires aligned byte, halfword or word writes at 0x%08x (size %u)", r->base + (uint32_t)off, size); return; }
    uint32_t address = r->base + (uint32_t)off;
    if (size < 4 && r->base == 0xe000e000u) {
        if (!((address >= 0xe000e400u && address + size <= 0xe000e420u) ||
              (address >= 0xe000ed18u && address + size <= 0xe000ed20u))) {
            sim_fail(r->sim, "Unsupported narrow PPB write at 0x%08x", address); return;
        }
        bool available;
        uint32_t old = bus_read(r->sim, address & ~3u, true, &available);
        uint32_t mask = ((1u << (size * 8)) - 1) << ((off & 3) * 8);
        value = (old & ~mask) | (((uint32_t)value << ((off & 3) * 8)) & mask);
    } else if (size == 1) value = (value & 255) * 0x01010101u;
    else if (size == 2) value = (value & 65535) * 0x00010001u;
    if (!bus_write(r->sim, address & ~3u, (uint32_t)value))
        sim_fail(r->sim, "Unsupported MMIO write at 0x%08x = 0x%08x", r->base + (uint32_t)off, (uint32_t)value);
}

static bool invalid_memory(uc_engine *cpu, uc_mem_type type, uint64_t address,
                           int size, int64_t value, void *data) {
    (void)cpu; (void)type; (void)value;
    sim_fail(data, "Invalid memory access at 0x%08llx (%d bytes)", (unsigned long long)address, size);
    return false;
}

uc_err sim_bus_map(sim_t *s) {
    uc_err err = UC_ERR_OK;
    if (!err) err = uc_mem_map(s->cpu, FLASH_BASE, FLASH_SIZE, UC_PROT_READ | UC_PROT_EXEC);
    if (!err) err = uc_mem_map(s->cpu, RAM_BASE, RAM_SIZE, UC_PROT_ALL);
    if (!err) err = uc_mem_write(s->cpu, FLASH_BASE, s->flash, FLASH_SIZE);
    if (!err) err = uc_mem_write(s->cpu, RAM_BASE, s->ram, RAM_SIZE);
    if (!err && (s->sdk_mode || s->rom_mode)) err = sim_rom_map(s);
    const uint32_t bases[34] = {0xd0000000u, 0x40014000u, 0x4001c000u,
                               0x40054000u, 0x40034000u, 0x40038000u, 0xe000e000u,
                               0x40044000u, 0x40048000u, 0x4003c000u, 0x40040000u,
                               0x40050000u, 0x4004c000u, 0x50200000u, 0x50300000u, 0x50000000u, 0x40010000u,
                               0x40000000u, 0x40004000u, 0x40008000u, 0x4000c000u, 0x40024000u,
                               0x40028000u, 0x4002c000u, 0x40058000u, 0x4006c000u, 0x50110000u,
                               0x40018000u, 0x40020000u, 0x18000000u, 0x14000000u, 0x40064000u, 0x40060000u, 0x4005c000u};
    for (unsigned i = 0; i < 34 && !err; i++) {
        s->regions[i] = (region_t){s, bases[i]};
        err = uc_mmio_map(s->cpu, bases[i], i && i != 6 ? 0x4000 : 0x1000,
                         read_callback, &s->regions[i], write_callback, &s->regions[i]);
    }
    uc_hook hook;
    if (!err) err = uc_hook_add(s->cpu, &hook, UC_HOOK_MEM_INVALID, (void *)invalid_memory, s, 1, 0);
    return err;
}

static uint16_t u16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

static uint32_t u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int in_region(uint32_t address, uint64_t length, uint32_t base, uint32_t size) {
    return address >= base && (uint64_t)address + length <= (uint64_t)base + size;
}

bool sim_bus_write_ram(sim_t *s, uint32_t a, const uint8_t *data, size_t length) {
    if (!in_region(a, length, RAM_BASE, RAM_SIZE)) return false;
    if (uc_mem_write(s->cpu, a, data, length) != UC_ERR_OK) return false;
    for (unsigned i = 0; i < s->segment_count; i++) {
        segment_t seg = s->segments[i];
        if ((seg.flags & 1) && (uint64_t)a < (uint64_t)seg.start + seg.size &&
            (uint64_t)a + length > seg.start)
            return uc_ctl_remove_cache(s->cpu, a, (uint64_t)a + length) == UC_ERR_OK;
    }
    return true;
}

bool sim_bus_dma_read(sim_t *s, uint32_t a, unsigned size, uint32_t *value) {
    if (size != 1 && size != 2 && size != 4) return false;
    if ((a >= 0xd0000000u && a < 0xd0001000u) || a >= 0xe0000000u) return false;
    if (a & (size - 1)) return false;
    *value = 0;
    if (in_region(a, size, FLASH_BASE, FLASH_SIZE) || in_region(a, size, RAM_BASE, RAM_SIZE)) {
        uint8_t bytes[4] = {0};
        if (uc_mem_read(s->cpu, a, bytes, size) != UC_ERR_OK) return false;
        for (unsigned i = 0; i < size; i++) *value |= (uint32_t)bytes[i] << (8 * i);
        return true;
    }
    bool available;
    *value = bus_read(s, a & ~3u, false, &available) >> ((a & 3) * 8);
    if (size < 4) *value &= (1u << (size * 8)) - 1;
    return available;
}

bool sim_bus_dma_write(sim_t *s, uint32_t a, unsigned size, uint32_t value) {
    if (size != 1 && size != 2 && size != 4) return false;
    if ((a >= 0xd0000000u && a < 0xd0001000u) || a >= 0xe0000000u) return false;
    if (a & (size - 1)) return false;
    if (in_region(a, size, RAM_BASE, RAM_SIZE)) {
        uint8_t bytes[4];
        for (unsigned i = 0; i < size; i++) bytes[i] = (uint8_t)(value >> (8 * i));
        return sim_bus_write_ram(s, a, bytes, size);
    }
    /* External I2C/SPI requests currently belong to the CPU handshake. */
    uint32_t base = a & ~0x3fffu;
    if (base == 0x4003c000u || base == 0x40040000u ||
        base == 0x40044000u || base == 0x40048000u) return false;
    if (size == 1) value = (value & 255) * 0x01010101u;
    if (size == 2) value = (value & 65535) * 0x00010001u;
    return bus_write(s, a & ~3u, value);
}

static int load_elf(sim_t *s, const uint8_t *data, size_t length, bool sdk) {
    if (!s || !data) return -1;
    if (length < 52 || memcmp(data, "\177ELF", 4) || data[4] != 1 || data[5] != 1 ||
        data[6] != 1 || u16(data + 16) != 2 || u16(data + 18) != 40 || u32(data + 20) != 1)
        return sim_fail(s, "Expected a little-endian ELF32 ARM executable");
    uint32_t entry = u32(data + 24), phoff = u32(data + 28);
    unsigned count = u16(data + 44), size = u16(data + 42);
    if (!count || count > MAX_SEGMENTS || size != 32 ||
        (uint64_t)phoff + (uint64_t)count * size > length)
        return sim_fail(s, "Invalid ELF program header table");
    segment_t segments[MAX_SEGMENTS];
    unsigned n = 0;
    bool executable = false, vectors = false;
    uint32_t stack = 0, reset = 0, vector_base = FLASH_BASE + (sdk ? 256u : 0u);
    for (unsigned i = 0; i < count; i++) {
        const uint8_t *p = data + phoff + i * size;
        if (u32(p) != 1) continue;
        uint32_t offset = u32(p + 4), addr = u32(p + 8), filesz = u32(p + 16);
        uint32_t memsz = u32(p + 20), flags = u32(p + 24);
        if (!memsz) continue;
        if (filesz > memsz || (uint64_t)offset + filesz > length ||
            !(in_region(addr, memsz, FLASH_BASE, FLASH_SIZE) || in_region(addr, memsz, RAM_BASE, RAM_SIZE)))
            return sim_fail(s, "Invalid or unmapped ELF load segment %u", i);
        uint32_t physical = u32(p + 12);
        if (sdk && filesz && physical != addr &&
            (!in_region(addr, memsz, RAM_BASE, RAM_SIZE) || !in_region(physical, filesz, FLASH_BASE, FLASH_SIZE)))
            return sim_fail(s, "Invalid SDK ELF load address in segment %u", i);
        if (sdk && filesz) for (unsigned j = 0; j < i; j++) {
            const uint8_t *q = data + phoff + j * size;
            if (u32(q) == 1 && u32(q + 16) &&
                (uint64_t)physical < (uint64_t)u32(q + 12) + u32(q + 16) &&
                (uint64_t)u32(q + 12) < (uint64_t)physical + filesz)
                return sim_fail(s, "Overlapping SDK ELF physical load segments");
        }
        for (unsigned j = 0; j < n; j++) {
            if ((uint64_t)addr < (uint64_t)segments[j].start + segments[j].size &&
                (uint64_t)segments[j].start < (uint64_t)addr + memsz)
                return sim_fail(s, "Overlapping ELF load segments");
        }
        segments[n++] = (segment_t){addr, memsz, flags};
        if ((flags & 1) && (entry & ~1u) >= addr && (uint64_t)(entry & ~1u) + 2 <= (uint64_t)addr + filesz)
            executable = true;
        if (addr <= vector_base && (uint64_t)vector_base + 8 <= (uint64_t)addr + filesz) {
            vectors = true;
            stack = u32(data + offset + vector_base - addr);
            reset = u32(data + offset + vector_base - addr + 4);
        }
    }
    bool reset_executable = false;
    for (unsigned i = 0; i < count; i++) {
        const uint8_t *p = data + phoff + i * size;
        uint32_t a = u32(p + 8);
        if (u32(p) == 1 && (u32(p + 24) & 1) && (reset & ~1u) >= a &&
            (uint64_t)(reset & ~1u) + 2 <= (uint64_t)a + u32(p + 16)) reset_executable = true;
    }
    if (!(entry & 1) || !executable || !vectors || !(reset & 1) || !reset_executable ||
        (!sdk && reset != entry) || (stack & 7) ||
        stack <= RAM_BASE || stack > RAM_BASE + RAM_SIZE)
        return sim_fail(s, sdk ? "SDK ELF requires executable Thumb entry/reset, vectors at flash+256, and aligned SRAM stack" :
                        "Direct load requires executable Thumb entry, matching flash vectors, and aligned SRAM stack");
    memset(s->flash, 0, FLASH_SIZE); memset(s->ram, 0, RAM_SIZE);
    for (unsigned i = 0; i < count; i++) {
        const uint8_t *p = data + phoff + i * size;
        if (u32(p) != 1 || !u32(p + 20)) continue;
        uint32_t addr = sdk ? u32(p + 12) : u32(p + 8), filesz = u32(p + 16);
        if (!filesz) continue;
        uint8_t *dst = addr >= RAM_BASE ? s->ram + addr - RAM_BASE : s->flash + addr - FLASH_BASE;
        memcpy(dst, data + u32(p + 4), filesz);
    }
    memcpy(s->segments, segments, n * sizeof(segment_t)); s->segment_count = n;
    s->entry = sdk ? reset : entry; s->stack = stack; s->vector_base = vector_base;
    s->sdk_mode = sdk; s->rom_mode = false; s->loaded = 1;
    return sim_reset(s);
}

int sim_load_elf(sim_t *s, const uint8_t *data, size_t length) { return load_elf(s, data, length, false); }
int sim_load_elf_sdk(sim_t *s, const uint8_t *data, size_t length) { return load_elf(s, data, length, true); }

int sim_peek_memory(sim_t *s, uint32_t address, uint8_t *out, size_t length) {
    if (!s || !out || !length || length > 65536) return -1;
    if (in_region(address, length, FLASH_BASE, FLASH_SIZE) || in_region(address, length, RAM_BASE, RAM_SIZE) ||
        ((s->sdk_mode || s->rom_mode) && in_region(address, length, 0, 0x4000)))
        return uc_mem_read(s->cpu, address, out, length) == UC_ERR_OK ? 0 : -1;
    if (length != 4 || (address & 3)) return -1;
    unsigned saved = s->active_core;
    if (sim_cpu_select(s, 0)) return -1;
    bool available;
    uint32_t value = bus_read(s, address, true, &available);
    if (sim_cpu_select(s, saved)) return -1;
    if (!available) return -1;
    for (unsigned i = 0; i < 4; i++) out[i] = (uint8_t)(value >> (8 * i));
    return 0;
}
