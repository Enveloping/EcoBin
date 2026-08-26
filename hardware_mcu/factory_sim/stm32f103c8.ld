ENTRY(Reset_Handler)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 20K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
    .isr_vector :
    {
        . = ALIGN(4);
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } > FLASH

    .text :
    {
        . = ALIGN(4);
        *(.text*)
        *(.rodata*)
        *(.ARM.extab*)
        . = ALIGN(4);
    } > FLASH

    .ARM.exidx :
    {
        *(.ARM.exidx*)
    } > FLASH

    .data :
    {
        . = ALIGN(4);
        _sdata = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > RAM AT > FLASH
    _sidata = LOADADDR(.data);
    _flash_end = LOADADDR(.data) + SIZEOF(.data);

    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > RAM

    /DISCARD/ :
    {
        *(.comment*)
        *(.note*)
        *(.ARM.attributes*)
    }
}

ASSERT(_flash_end <= ORIGIN(FLASH) + LENGTH(FLASH),
       "factory simulation firmware exceeds STM32F103C8 flash")
ASSERT(_ebss <= ORIGIN(RAM) + LENGTH(RAM),
       "factory simulation firmware exceeds STM32F103C8 RAM")
