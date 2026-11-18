ENTRY(reset_handler)
MEMORY {
    FLASH (rx) : ORIGIN = 0x10000000, LENGTH = 2M
    RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 264K
}
PHDRS { flash PT_LOAD FLAGS(5); ram PT_LOAD FLAGS(6); }
SECTIONS {
    .vectors : { KEEP(*(.vectors)) } > FLASH :flash
    .text : { *(.text*) *(.rodata*) } > FLASH :flash
    .data : { *(.data*) } > RAM :ram
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM :ram
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) }
}
