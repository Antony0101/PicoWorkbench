/* Explicit SDK-mode ROM ABI shim, not a copy or emulation of the boot ROM. */
#include "sim_internal.h"
#include <stdlib.h>
#include <string.h>

/* Table lookup at 0x200, functions at 0x300 + 16*i. */
static const uint16_t codes[] = {0x334c,0x3354,0x3350,0x3352,0x434d,0x534d,0x3453,0x3443};
static void put16(uint8_t *p, uint16_t v) { p[0] = v & 255; p[1] = v >> 8; }

uc_err sim_rom_map(sim_t *s) {
    if (s->rom_mode) {
        uc_err err = uc_mem_map(s->cpu, 0, 0x4000, UC_PROT_READ | UC_PROT_EXEC);
        if (!err) err = uc_mem_write(s->cpu, 0, s->rom, 0x4000);
        return err;
    }
    uint8_t rom[0x4000] = {0};
    rom[0x10] = 'M'; rom[0x11] = 'u'; rom[0x12] = 1; rom[0x13] = 3;
    put16(rom + 0x14, 0x100); put16(rom + 0x16, 0x180); put16(rom + 0x18, 0x201);
    for (unsigned i = 0; i < sizeof(codes) / sizeof(codes[0]); i++) {
        put16(rom + 0x100 + 4 * i, codes[i]);
        put16(rom + 0x102 + 4 * i, 0x301 + 16 * i);
    }
    /* V3 soft-float/double descriptors allow the stock SDK's runtime init to
     * copy its tables even for integer-only programs. Calls through these
     * entries fault explicitly below: actual arithmetic requires ROM mode. */
    put16(rom + 0x180, 0x4653); put16(rom + 0x182, 0x800); /* SF */
    put16(rom + 0x184, 0x4453); put16(rom + 0x186, 0x900); /* SD */
    rom[0x7fe] = 32;
    for (unsigned i = 0; i < 32; i++) {
        put16(rom + 0x800 + 4 * i, 0x401 + 4 * i);
        put16(rom + 0x900 + 4 * i, 0x501 + 4 * i);
    }
    uc_err err = uc_mem_map(s->cpu, 0, sizeof(rom), UC_PROT_READ | UC_PROT_EXEC);
    if (!err) err = uc_mem_write(s->cpu, 0, rom, sizeof(rom));
    return err;
}

static bool range(uint32_t a, uint32_t n, uint32_t base, uint32_t size) {
    return a >= base && (uint64_t)a + n <= (uint64_t)base + size;
}

static bool memop(sim_t *s, unsigned fn, uint32_t *r) {
    uint32_t a = r[0], b = r[1], n = r[2];
    bool copy = fn == 4 || fn == 7;
    if (!n) return true;
    if (!range(a, n, RAM_BASE, RAM_SIZE) || (copy &&
        !(range(b, n, RAM_BASE, RAM_SIZE) || range(b, n, FLASH_BASE, FLASH_SIZE) || range(b, n, 0, 0x4000)))) {
        sim_fail(s, "ROM memory operation outside mapped memory: dst=0x%08x src/value=0x%08x size=%u", a, b, n);
        return false;
    }
    if ((fn == 6 || fn == 7) && ((a | n | (copy ? b : 0)) & 3)) {
        sim_fail(s, "ROM aligned memory operation requires word-aligned addresses and size"); return false;
    }
    uint8_t *data = malloc(n);
    if (!data) { sim_fail(s, "Cannot allocate ROM memory operation buffer"); return false; }
    bool ok = true;
    if (copy) ok = uc_mem_read(s->cpu, b, data, n) == UC_ERR_OK;
    else memset(data, b & 255, n);
    if (ok) ok = sim_bus_write_ram(s, a, data, n);
    free(data);
    if (!ok) sim_fail(s, "ROM memory operation failed at 0x%08x", a);
    return ok;
}

bool sim_rom_call(sim_t *s, uint32_t address) {
    if (((address >= 0x400 && address < 0x480) ||
         (address >= 0x500 && address < 0x580)) && !(address & 3)) {
        sim_scheduler_instruction(s);
        sim_fail(s, "Unsupported SDK shortcut ROM floating-point function %s slot %u; use ROM startup",
                 address < 0x500 ? "SF" : "SD", (address & 0x7f) / 4);
        return true;
    }
    if (address != 0x200 && (address < 0x300 || address >= 0x380 || (address & 15))) return false;
    uint32_t r[3] = {0}, result = 0, lr = 0;
    uc_reg_read(s->cpu, UC_ARM_REG_R0, &r[0]); uc_reg_read(s->cpu, UC_ARM_REG_R1, &r[1]);
    uc_reg_read(s->cpu, UC_ARM_REG_R2, &r[2]); uc_reg_read(s->cpu, UC_ARM_REG_LR, &lr);
    sim_scheduler_instruction(s);
    if (s->reason == SIM_FAULT) return true;
    if (address == 0x200) {
        if (r[0] == 0x100) {
            for (unsigned i = 0; i < sizeof(codes) / sizeof(codes[0]); i++)
                if (r[1] == codes[i]) result = 0x301 + 16 * i;
        }
        if (r[0] == 0x180) {
            if (r[1] == 0x4653) result = 0x800;
            else if (r[1] == 0x4453) result = 0x900;
        }
        if (!result) { sim_fail(s, "Unsupported ROM lookup: table=0x%08x code=0x%04x", r[0], r[1]); return true; }
    } else {
        unsigned fn = (address - 0x300) / 16;
        if (fn == 0) { result = 32; for (unsigned i = 0; i < 32; i++) if (r[0] & (1u << (31 - i))) { result = i; break; } }
        else if (fn == 1) { result = 32; for (unsigned i = 0; i < 32; i++) if (r[0] & (1u << i)) { result = i; break; } }
        else if (fn == 2) { for (unsigned i = 0; i < 32; i++) result += (r[0] >> i) & 1; }
        else if (fn == 3) { for (unsigned i = 0; i < 32; i++) result |= ((r[0] >> i) & 1) << (31 - i); }
        else { if (!memop(s, fn, r)) return true; result = r[0]; }
    }
    if (!(lr & 1)) { sim_fail(s, "ROM shim return requires a Thumb link address"); return true; }
    uc_reg_write(s->cpu, UC_ARM_REG_R0, &result);
    uc_reg_write(s->cpu, UC_ARM_REG_PC, &lr);
    s->redirected = 1; sim_cpu_stop(s);
    return true;
}
