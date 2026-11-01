/* Linker script for DSO-TC3 Custom Firmware Extension */
ENTRY(extension_dispatch)

MEMORY
{
    FLASH_EXT (rx)  : ORIGIN = 0x0805E000, LENGTH = 132K
    RAM_EXT   (rwx) : ORIGIN = 0x20013000, LENGTH = 16K
}

SECTIONS
{
    .text : ALIGN(4)
    {
        /* Ensure extension_dispatch is at the absolute start of FLASH_EXT (0x0805E000) */
        KEEP(*(.text.entry))
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
        _etext = .;
    } > FLASH_EXT

    .data : ALIGN(4)
    {
        _sdata = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > RAM_EXT AT > FLASH_EXT

    _sidata = LOADADDR(.data);

    .bss : ALIGN(4)
    {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > RAM_EXT

    /DISCARD/ :
    {
        *(.ARM.exidx*)
        *(.note*)
        *(.comment*)
    }
}

/* Stock firmware symbols (device addresses) */
PROVIDE(stock_console_dispatch = 0x08013f08);
/* gain code -> 0x2000000c, then GPIO front-end mux/relay; Thumb bit set */
PROVIDE(stock_apply_vdiv_gain = 0x08007348 | 1);
PROVIDE(g_ScopeState = 0x20000668);
PROVIDE(g_AdcRawBuffer = 0x2000b07c);

/* UI layer. All Thumb entry points, bit 0 set. */
PROVIDE(stock_ui_adjust       = 0x080116b0 | 1);  /* adjust(delta): edits the field selected by the cursor, redraws it */
PROVIDE(stock_refresh_runstop = 0x08011d20 | 1);  /* refresh(0): RUN/STOP indicator from +0xa1 */
PROVIDE(stock_widget_show     = 0x08025b6c | 1);  /* (widget, flags) */
PROVIDE(stock_capture_start   = 0x080073b8 | 1);
PROVIDE(stock_single_wait     = 0x080074d0 | 1);  /* blocking single-shot wait, as in the stock hold-key path */
PROVIDE(stock_key_consumer    = 0x08007cb0 | 1);  /* called once per main-loop pass; our hook wraps it */
PROVIDE(g_UiState             = 0x20000140);      /* +4 cursor id, +0x2c run/stop widget */

/* Screen capture: display driver flush callback */
PROVIDE(stock_flush_cb      = 0x0801984c | 1);  /* (drv, area, color_p): sets the LCD window, pushes RGB565, flush_ready */
PROVIDE(stock_obj_invalidate = 0x08027d68 | 1); /* lv_obj_invalidate(obj) */

/* Roll path: ADC1_2 IRQ handler, vector 34 at 0x08004088 */
PROVIDE(stock_adc_isr = 0x08004a4c | 1);

/* Signal generator: (float amp in s0, wave r0, freq r1, duty r2); builds the DAC table and starts DAC DMA / TIM8 PWM */
PROVIDE(stock_gen_apply = 0x08005da0 | 1);
/* Generator screen update (build, delta): wave += delta with label recolor and selector strip, then the value labels,
 * row visibility, preview line points (0x2000155c) and FUN_08005da0. Called with delta 0 it only refreshes. */
PROVIDE(stock_gen_screen_update = 0x08012f48 | 1);
