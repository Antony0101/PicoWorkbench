/* Functional W25Q16-compatible serial flash, SSI and QSPI control registers.
 * Transfers complete immediately; ROM and boot2 execute their own instructions. */
#include "sim_internal.h"
#include <string.h>

void sim_flash_reset(sim_t *s) {
    memset(&s->flash_hw, 0, sizeof(s->flash_hw));
    flash_t *f = &s->flash_hw;
    for (unsigned i = 0; i < 6; i++) { f->io[i] = 31; f->pads[i] = 0x56; }
    f->pads[1] = 0x5a; f->xip_ctrl = 3;
}

static void end_transaction(sim_t *s) {
    flash_t *f = &s->flash_hw;
    if (f->active && f->wel && (f->command == 0x20 || f->command == 0xd8) && f->position >= 3) {
        unsigned size = f->command == 0x20 ? 4096 : 65536;
        uint32_t a = f->address & ~(size - 1u);
        if ((uint64_t)a + size > FLASH_SIZE) sim_fail(s, "Flash erase outside 2 MiB at 0x%08x", a);
        else {
            memset(s->flash + a, 0xff, size);
            uc_mem_write(s->cpu, FLASH_BASE + a, s->flash + a, size);
            uc_ctl_remove_cache(s->cpu, FLASH_BASE + a, FLASH_BASE + a + size);
        }
        f->wel = false;
    }
    if (f->command == 2 || f->command == 1 || f->command == 0x31) f->wel = false;
    f->active = false; f->position = f->address = f->command = 0;
}

void sim_flash_reset_mask(sim_t *s, uint32_t mask) {
    flash_t *f = &s->flash_hw;
    if (mask & (1u << 6)) {
        end_transaction(s); f->force_cs = 0;
        for (unsigned i = 0; i < 6; i++) f->io[i] = 31;
    }
    if (mask & (1u << 9)) {
        for (unsigned i = 0; i < 6; i++) f->pads[i] = 0x56;
        f->pads[1] = 0x5a;
    }
}

static uint8_t exchange(sim_t *s, uint8_t value) {
    flash_t *f = &s->flash_hw;
    if (f->force_cs == 3) return 0xff;
    if (!f->active) {
        f->active = true; f->command = value; f->position = f->address = 0;
        if (value == 6) f->wel = true;
        if (value == 4) f->wel = false;
        /* Exit-XIP clocks use 0x00/0xff. Supported commands are explicit. */
        if (value != 0 && value != 0xff && value != 6 && value != 4 && value != 1 &&
            value != 2 && value != 3 && value != 5 && value != 0x35 && value != 0x31 &&
            value != 0x20 && value != 0xd8 && value != 0x9f && value != 0x4b && value != 0x5a)
            sim_fail(s, "Unsupported serial flash command 0x%02x", value);
        return 0;
    }
    unsigned pos = f->position++;
    if (f->command == 3 || f->command == 2 || f->command == 0x20 || f->command == 0xd8 || f->command == 0x5a) {
        if (pos < 3) { f->address = (f->address << 8) | value; return 0; }
        if (f->command == 0x5a) return 0xff; /* SFDP absent; JEDEC fallback is available. */
        if (f->command == 0x20 || f->command == 0xd8) return 0;
        uint32_t a = f->command == 2 ? (f->address & ~255u) | ((f->address + pos - 3) & 255) : f->address + pos - 3;
        if (a >= FLASH_SIZE) { sim_fail(s, "Serial flash access outside 2 MiB at 0x%08x", a); return 0xff; }
        if (f->command == 2 && f->wel) {
            s->flash[a] &= value; /* NOR program clears bits, erase sets them. */
            uc_mem_write(s->cpu, FLASH_BASE + a, s->flash + a, 1);
            uc_ctl_remove_cache(s->cpu, FLASH_BASE + (a & ~255u), FLASH_BASE + (a & ~255u) + 256);
        }
        return f->command == 3 ? s->flash[a] : 0;
    }
    if (f->command == 5) return (f->sr1 & ~3u) | (f->wel ? 2 : 0);
    if (f->command == 0x35) return f->sr2;
    if (f->command == 1 && f->wel) { if (!pos) f->sr1 = value & ~3u; else if (pos == 1) f->sr2 = value; }
    if (f->command == 0x31 && f->wel && !pos) f->sr2 = value;
    if (f->command == 0x9f) { static const uint8_t id[] = {0xef,0x40,0x15}; return id[pos % 3]; }
    if (f->command == 0x4b) return pos < 4 ? 0 : (uint8_t)(0x42 + (pos - 4) % 8);
    return 0xff;
}

uint32_t sim_flash_read(sim_t *s, uint32_t a, bool peek, bool *found) {
    flash_t *f = &s->flash_hw;
    uint32_t base = a & ~0x3fffu, off = a & 0xfff;
    *found = true;
    if (a == 0xd0000008) return 2; /* BOOTSEL strap deasserted */
    if (base == 0x40018000 && off < 48) return off & 4 ? f->io[off / 8] : 0;
    if (base == 0x40020000) {
        if (!off) return 0;
        if (off >= 4 && off <= 24) return f->pads[(off - 4) / 4];
    }
    if (base == 0x40064000 && off == 8) return 0x00110000; /* power-on flags, rescue clear */
    if (base == 0x14000000) {
        if (!off) return f->xip_ctrl;
        if (off == 4) return 0;
        if (off == 8) return 2; /* flush ready, streaming FIFO empty */
        if (off == 12 || off == 16) return 0;
    }
    if (base == 0x18000000) {
        if (off == 0x20) return 0;
        if (off == 0x24) return f->count;
        if (off == 0x28) return 6 | (f->count ? 8 : 0) | (f->count == 16 ? 16 : 0);
        if (off == 0x48) return 0;
        if (off == 0x60) {
            uint32_t v = f->count ? f->rx[f->head] : 0;
            if (!peek && f->count) {
                f->head = (f->head + 1) % 16; f->count--;
                if (!f->count && !f->force_cs) end_transaction(s);
            }
            return v;
        }
        if (off == 0 || off == 4 || off == 8 || off == 0x10 || off == 0x14 || off == 0x18 || off == 0x1c || off == 0xf0 || off == 0xf4) return f->ssi[off / 4];
    }
    *found = false; return 0;
}

bool sim_flash_write(sim_t *s, uint32_t a, uint32_t value) {
    flash_t *f = &s->flash_hw;
    uint32_t base = a & ~0x3fffu, off = a & 0xfff, alias = (a >> 12) & 3;
    if (base == 0x40018000 && off < 48 && (off & 4)) {
        unsigned pin = off / 8;
        uint32_t v = atomic_value(f->io[pin], value, alias);
        if (pin == 1) {
            unsigned force = (v >> 8) & 3;
            if (force != f->force_cs) end_transaction(s);
            f->force_cs = force;
        }
        f->io[pin] = v; return true;
    }
    if (base == 0x40020000 && off >= 4 && off <= 24) {
        unsigned pin = (off - 4) / 4; f->pads[pin] = atomic_value(f->pads[pin], value, alias) & 255; return true;
    }
    if (base == 0x14000000) {
        if (!off) { f->xip_ctrl = atomic_value(f->xip_ctrl, value, alias) & 11; return true; }
        if (off == 4 || off == 12 || off == 16) return true;
    }
    if (base == 0x18000000) {
        if (off == 0x60) {
            if (!(f->ssi[2] & 1)) { sim_fail(s, "SSI transfer while disabled"); return true; }
            if (((f->ssi[0] >> 8) & 3) == 3) {
                /* Boot2's dummy quad read enters continuous XIP mode. */
                if (f->count < 16) f->rx[(f->head + f->count++) % 16] = 0;
                return true;
            }
            uint8_t response = exchange(s, value & 255);
            if (f->count == 16) sim_fail(s, "SSI receive FIFO overflow");
            else f->rx[(f->head + f->count++) % 16] = response;
            return true;
        }
        if (off == 0 || off == 4 || off == 8 || off == 0x10 || off == 0x14 || off == 0x18 || off == 0x1c || off == 0xf0 || off == 0xf4) {
            f->ssi[off / 4] = value;
            if (off == 8 && !(value & 1)) { end_transaction(s); f->head = f->count = 0; }
            return true;
        }
    }
    return false;
}
